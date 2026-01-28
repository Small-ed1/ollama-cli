"""Deep research pipeline.

This module provides a small, self-contained research flow that:
- plans a handful of search queries
- gathers sources via SearxNG (web_search)
- opens and extracts text from top sources
- synthesizes a cited report with an Ollama model

It is designed to be used from both the interactive shell and a CLI subcommand.
"""

import json
import re
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from .client import OllamaClient
from .config import DEFAULT_SEARXNG_URL
from .tools.web_tools import WebTools


_CODE_BLOCK = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)


@dataclass
class ResearchPreset:
    """Budgets and knobs for research."""

    name: str
    max_planned_queries: int
    results_per_query: int
    max_sources_open: int
    max_source_chars: int
    recency_days: int


PRESETS: Dict[str, ResearchPreset] = {
    "quick": ResearchPreset(
        name="quick",
        max_planned_queries=2,
        results_per_query=6,
        max_sources_open=5,
        max_source_chars=6000,
        recency_days=365,
    ),
    "standard": ResearchPreset(
        name="standard",
        max_planned_queries=4,
        results_per_query=10,
        max_sources_open=10,
        max_source_chars=9000,
        recency_days=365,
    ),
    "deep": ResearchPreset(
        name="deep",
        max_planned_queries=7,
        results_per_query=15,
        max_sources_open=16,
        max_source_chars=12000,
        recency_days=365,
    ),
}


def _safe_json_from_text(text: str) -> Optional[Dict[str, Any]]:
    """Best-effort JSON extraction from raw model output."""
    s = (text or "").strip()
    if not s:
        return None

    # Direct parse
    try:
        data = json.loads(s)
        return data if isinstance(data, dict) else None
    except Exception:
        pass

    # Code block parse
    for m in _CODE_BLOCK.finditer(s):
        try:
            data = json.loads(m.group(1))
            return data if isinstance(data, dict) else None
        except Exception:
            continue

    return None


def _chat_once(
    client: OllamaClient,
    model: str,
    messages: List[Dict[str, Any]],
    options: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Run a single non-streaming chat call and return the response dict."""
    # OllamaClient.chat returns an iterator; for non-stream it yields exactly one object.
    out: Dict[str, Any] = {}
    for chunk in client.chat(messages, model, stream=False, options=options):
        out = chunk
    return out


def _content_from_chat_response(resp: Dict[str, Any]) -> str:
    msg = resp.get("message", {}) or {}
    return msg.get("content") or ""


def plan_queries(
    client: OllamaClient,
    model: str,
    query: str,
    preset: ResearchPreset,
) -> Tuple[List[str], List[str]]:
    """Ask the model for a small plan: search queries + subquestions."""
    system = (
        "You are a research planner. Output ONLY valid JSON. "
        "Return an object with keys: search_queries (array of strings), subquestions (array of strings). "
        "Keep search_queries concise and specific."
    )
    user = {
        "query": query,
        "max_search_queries": preset.max_planned_queries,
        "notes": "Prefer authoritative sources. Include at least one query targeting official docs/specs if relevant.",
    }
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": json.dumps(user, ensure_ascii=False)},
    ]
    resp = _chat_once(client, model, messages, options={"temperature": 0.2})
    data = _safe_json_from_text(_content_from_chat_response(resp)) or {}

    raw_queries = data.get("search_queries", [])
    raw_subq = data.get("subquestions", [])

    queries: List[str] = []
    if isinstance(raw_queries, list):
        for q in raw_queries:
            if isinstance(q, str):
                q = q.strip()
                if q and q not in queries:
                    queries.append(q)

    subquestions: List[str] = []
    if isinstance(raw_subq, list):
        for sq in raw_subq:
            if isinstance(sq, str):
                sq = sq.strip()
                if sq and sq not in subquestions:
                    subquestions.append(sq)

    if not queries:
        queries = [query]

    return queries[: preset.max_planned_queries], subquestions[: max(0, preset.max_planned_queries * 2)]


def run_deep_research(
    *,
    client: OllamaClient,
    model: str,
    query: str,
    preset_name: str = "standard",
    seed_urls: Optional[List[str]] = None,
    searxng_url: Optional[str] = None,
) -> str:
    """Run a bounded deep research pass and return a cited report."""
    preset = PRESETS.get(preset_name, PRESETS["standard"])
    web = WebTools(searxng_url=searxng_url or DEFAULT_SEARXNG_URL)

    started_at = time.time()
    planned_queries, subquestions = plan_queries(client, model, query, preset)

    # Gather candidate URLs
    urls: List[str] = []
    sources: List[Dict[str, Any]] = []

    if seed_urls:
        for u in seed_urls:
            u = (u or "").strip()
            if u and u not in urls:
                urls.append(u)
    else:
        for q in planned_queries:
            results = web.search(q, count=preset.results_per_query, recency_days=preset.recency_days)
            for r in results:
                if r.url and r.url not in urls:
                    urls.append(r.url)
                    sources.append({"title": r.title, "url": r.url, "snippet": r.snippet})
            if len(urls) >= preset.max_sources_open:
                break

    # Open sources
    opened: List[Dict[str, Any]] = []
    for u in urls[: preset.max_sources_open]:
        try:
            opened_doc = web.open_url(u, mode="auto", max_chars=preset.max_source_chars)
        except Exception:
            continue
        opened.append(opened_doc)

    if not opened:
        raise RuntimeError(
            "No sources could be fetched/opened. "
            "If using web search, verify your SearxNG instance and SEARXNG_URL. "
            "Alternatively, provide seed URLs."
        )

    # Build report prompt
    packet = {
        "query": query,
        "subquestions": subquestions,
        "planned_queries": planned_queries,
        "sources": sources[: preset.max_sources_open],
        "opened": opened,
        "elapsed_s": round(time.time() - started_at, 2),
        "instructions": {
            "citation_style": "Use inline bracket citations like [1], [2] referencing the numbered Sources list you include at the end.",
            "be_explicit_about_uncertainty": True,
            "prefer_primary_sources": True,
        },
    }

    system = (
        "You are a deep research assistant. Write a concise, high-signal report with citations. "
        "Use only the provided sources. If evidence is weak or conflicting, say so. "
        "End with a 'Sources' section listing numbered sources with title and URL."
    )
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": json.dumps(packet, ensure_ascii=False)},
    ]
    resp = _chat_once(client, model, messages, options={"temperature": 0.3})
    return _content_from_chat_response(resp).strip()
