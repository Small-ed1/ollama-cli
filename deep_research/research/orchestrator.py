from __future__ import annotations

import hashlib
import json
import time
from typing import Any, Dict, List, Optional, Tuple

from .types import (
    Budgets, Parameters, ResearchState, ResearchPlan,
    Source, Note, Claim, Conflict, SourceRef, Importance
)
from .searxng import SearxNG
from .kiwix import KiwixHTTP
from .fetch import fetch_text
from .ranker import score_sources, select_top
from .ollama_client import OllamaClient
from . import prompts


def _id(prefix: str, s: str) -> str:
    return hashlib.sha256(f"{prefix}:{s}".encode("utf-8")).hexdigest()[:16]


class Orchestrator:
    def __init__(
        self,
        ollama: OllamaClient,
        searx: SearxNG,
        kiwix: KiwixHTTP,
        models: Dict[str, str],
        options: Dict[str, Dict[str, Any]],
    ):
        self.ollama = ollama
        self.searx = searx
        self.kiwix = kiwix
        self.models = models
        self.options = options

    def run(self, state: ResearchState) -> str:
        t0 = time.time()

        # Stage 1: plan
        state.plan = self._plan(state)

        # Iterative gather/extract loop
        diversity_seen: Dict[str, int] = {}
        unresolved_conflicts: List[str] = []

        for it in range(state.budgets.max_iterations):
            if time.time() - t0 > state.budgets.time_limit_s:
                break

            candidates: List[Source] = []

            # Balanced gather: Kiwix grounding + web freshness each iteration (bounded)
            if state.kiwix_queries < state.budgets.max_kiwix_queries:
                for zim in (state.parameters.zim_targets or []):
                    if state.kiwix_queries >= state.budgets.max_kiwix_queries:
                        break
                    if it == 0:
                        qset = state.plan.kiwix_queries
                    else:
                        # later iterations: focus on conflict watch + missing pieces
                        qset = state.plan.conflict_watch[:2] + state.plan.kiwix_queries[:1]
                    for q in qset:
                        if state.kiwix_queries >= state.budgets.max_kiwix_queries:
                            break
                        hits = self.kiwix.search(q, zim=zim, max_hits=8)
                        state.kiwix_queries += 1
                        candidates.extend(hits)

            if state.searx_queries < state.budgets.max_searx_queries:
                qset = state.plan.searx_queries if it == 0 else (state.plan.conflict_watch + state.plan.searx_queries[:2])
                for q in qset[: max(1, 3)]:
                    if state.searx_queries >= state.budgets.max_searx_queries:
                        break
                    hits = self.searx.search(
                        q,
                        lang="en",
                        engines=state.parameters.searx_engines,
                        domains_allow=state.parameters.domain_allowlist,
                        domains_block=state.parameters.domain_blocklist,
                        max_results=15,
                    )
                    state.searx_queries += 1
                    candidates.extend(hits)

            # Dedupe candidates by source_id
            uniq: Dict[str, Source] = {}
            for c in candidates:
                if c.source_id not in uniq:
                    uniq[c.source_id] = c
            candidates = list(uniq.values())

            # Score + select
            score_sources(candidates, state.query_clarified, diversity_seen)
            to_open = select_top(
                candidates,
                max_take=max(1, min(8, state.budgets.max_sources_opened - state.opened_sources)),
                min_diversity=state.budgets.min_source_diversity,
            )

            # Open/fetch + extract notes
            for src in to_open:
                if state.opened_sources >= state.budgets.max_sources_opened:
                    break
                if time.time() - t0 > state.budgets.time_limit_s:
                    break

                opened = self._open_source(state, src)
                if not opened:
                    continue

                diversity_seen[src.publisher] = diversity_seen.get(src.publisher, 0) + 1
                state.sources[src.source_id] = src
                state.opened_sources += 1

                new_notes, possible_conflicts = self._extract_notes(state, src)
                for n in new_notes:
                    state.notes[n.note_id] = n

                unresolved_conflicts.extend(possible_conflicts)

            # Build claims from notes (lightweight)
            self._build_claims(state)

            # Stop early if we're meeting definition-of-done and not finding new notes
            if self._definition_of_done_met(state) and self._diminishing_returns(state):
                break

            # Conflict resolution sub-loop (bounded)
            if state.parameters.conflict_policy == "resolve_if_possible":
                self._resolve_conflicts(state, unresolved_conflicts, t0)

        # Final write
        return self._write_report(state)

    # ------------------ stages ------------------

    def _plan(self, state: ResearchState) -> ResearchPlan:
        messages = [
            {"role": "system", "content": prompts.PLANNER_SYSTEM},
            {"role": "user", "content": state.query_clarified},
        ]
        resp = self.ollama.chat(
            model=self.models["planner"],
            messages=messages,
            options=self.options.get("planner"),
        )
        content = self.ollama.content(resp).strip()
        data = _safe_json(content) or {}
        return ResearchPlan(
            subquestions=data.get("subquestions", []),
            kiwix_queries=data.get("kiwix_queries", []),
            searx_queries=data.get("searx_queries", []),
            definition_of_done=data.get("definition_of_done", []),
            conflict_watch=data.get("conflict_watch", []),
        )

    def _open_source(self, state: ResearchState, src: Source) -> bool:
        try:
            if src.kind == "web":
                if state.web_fetches >= state.budgets.max_web_fetches:
                    return False
                assert src.ref.url
                text, h = fetch_text(src.ref.url)
                src.content_text = text
                src.content_hash = h
                state.web_fetches += 1
                return True

            if src.kind == "kiwix":
                if state.kiwix_opens >= state.budgets.max_kiwix_opens:
                    return False
                zim = src.ref.zim or ""
                title = src.ref.title
                article_id = src.ref.article_id
                text = self.kiwix.open(zim=zim, title=title, article_id=article_id)
                src.content_text = text
                src.content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
                state.kiwix_opens += 1
                return True

        except Exception:
            return False

        return False

    def _extract_notes(self, state: ResearchState, src: Source) -> Tuple[List[Note], List[str]]:
        if not src.content_text:
            return [], []

        payload = {
            "source": {
                "title": src.title,
                "publisher": src.publisher,
                "ref": src.ref.url or f"{src.ref.zim}:{src.ref.title or src.ref.article_id}",
                "published_at": src.published_at,
            },
            "text": src.content_text[:12000],  # cap to control token use
        }

        messages = [
            {"role": "system", "content": prompts.EXTRACTOR_SYSTEM},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ]
        resp = self.ollama.chat(
            model=self.models["extractor"],
            messages=messages,
            options=self.options.get("extractor"),
        )
        data = _safe_json(self.ollama.content(resp)) or {}
        notes_raw = data.get("notes", [])
        conflicts = data.get("possible_conflicts", []) or []

        notes: List[Note] = []
        for i, n in enumerate(notes_raw):
            quote = (n.get("quote") or "").strip()
            paraphrase = (n.get("paraphrase") or "").strip()
            tag = n.get("tag") or "context"
            if not quote or not paraphrase:
                continue
            nid = _id("note", f"{src.source_id}:{i}:{paraphrase[:60]}")
            notes.append(
                Note(
                    note_id=nid,
                    source_id=src.source_id,
                    quote=quote[:300],
                    paraphrase=paraphrase,
                    tag=tag,  # type: ignore
                    entities=n.get("entities") or [],
                    units=n.get("units"),
                )
            )

        return notes, [str(c) for c in conflicts][:6]

    def _build_claims(self, state: ResearchState) -> None:
        """
        Minimal claim builder: group notes by tag+entity-ish text.
        You can replace this with a model-assisted claim generator later.
        """
        # simplistic: top N notes become "claims" (good enough to start)
        existing = set(state.claims.keys())
        for n in list(state.notes.values())[:40]:
            cid = _id("claim", n.note_id)
            if cid in existing:
                continue
            importance: Importance = "key" if n.tag in ("howto", "number", "warning") else "minor"
            state.claims[cid] = Claim(
                claim_id=cid,
                text=n.paraphrase,
                note_ids=[n.note_id],
                source_ids=[n.source_id],
                status="supported",  # type: ignore
                confidence=0.65,
                importance=importance,
            )

    def _resolve_conflicts(self, state: ResearchState, conflict_terms: List[str], t0: float) -> None:
        """
        Bounded conflict resolution:
        - Use 1-2 targeted web searches per iteration to find authoritative clarification.
        - If still unresolved, conflicts are reported in output (writer stage).
        """
        if not conflict_terms:
            return

        # pick a couple terms to chase
        terms = []
        for c in conflict_terms:
            c = c.strip()
            if c and c not in terms:
                terms.append(c)
            if len(terms) >= 2:
                break
        if not terms:
            return

        for term in terms:
            if time.time() - t0 > state.budgets.time_limit_s:
                return
            if state.searx_queries >= state.budgets.max_searx_queries:
                return

            q = f"{state.query_clarified} {term} changed deprecated official"
            hits = self.searx.search(q, lang="en", engines=state.parameters.searx_engines, max_results=10)
            state.searx_queries += 1

            # open 1-2 best
            score_sources(hits, q, {})
            to_open = select_top(hits, max_take=2, min_diversity=1)
            for src in to_open:
                if state.opened_sources >= state.budgets.max_sources_opened:
                    return
                if state.web_fetches >= state.budgets.max_web_fetches:
                    return
                if self._open_source(state, src):
                    state.sources[src.source_id] = src
                    state.opened_sources += 1
                    new_notes, _ = self._extract_notes(state, src)
                    for n in new_notes:
                        state.notes[n.note_id] = n
            self._build_claims(state)

    def _definition_of_done_met(self, state: ResearchState) -> bool:
        # Heuristic: if we have at least one note per subquestion-ish after some work.
        if not state.plan:
            return False
        if len(state.notes) < max(6, len(state.plan.subquestions)):
            return False
        return True

    def _diminishing_returns(self, state: ResearchState) -> bool:
        # Very basic: once we have a decent number of notes and claims, slow down.
        return len(state.notes) > 25 and len(state.claims) > 20

    def _write_report(self, state: ResearchState) -> str:
        # Build a compact evidence packet for the writer (don't dump everything).
        sources_ranked = list(state.sources.values())
        sources_ranked.sort(key=lambda s: s.score, reverse=True)

        # anti-drowning: cap cited sources
        cited_sources = sources_ranked[: state.budgets.max_sources_cited]

        # include notes/claims that reference those sources
        cited_ids = {s.source_id for s in cited_sources}
        notes = [n for n in state.notes.values() if n.source_id in cited_ids]
        claims = [c for c in state.claims.values() if any(sid in cited_ids for sid in c.source_ids)]

        packet = {
            "query": state.query_original,
            "clarified_query": state.query_clarified,
            "subquestions": state.plan.subquestions if state.plan else [],
            "claims": [
                {
                    "text": c.text,
                    "importance": c.importance,
                    "status": c.status,
                    "confidence": c.confidence,
                    "sources": c.source_ids,
                }
                for c in claims[:60]
            ],
            "notes": [
                {
                    "text": n.paraphrase,
                    "tag": n.tag,
                    "quote": n.quote,
                    "source_id": n.source_id,
                }
                for n in notes[:80]
            ],
            "sources": [
                {
                    "source_id": s.source_id,
                    "title": s.title,
                    "publisher": s.publisher,
                    "ref": s.ref.url or f"Kiwix:{s.ref.zim}/{s.ref.title or s.ref.article_id}",
                    "published_at": s.published_at,
                }
                for s in cited_sources
            ],
            "policies": {
                "documentation_required": state.parameters.archwiki_priority,
                "conflict_policy": state.parameters.conflict_policy,
                "citation_style": state.parameters.citation_style,
            },
        }

        messages = [
            {"role": "system", "content": prompts.WRITER_SYSTEM},
            {"role": "user", "content": json.dumps(packet, ensure_ascii=False)},
        ]
        resp = self.ollama.chat(
            model=self.models["writer"],
            messages=messages,
            options=self.options.get("writer"),
        )
        return self.ollama.content(resp).strip()


def _safe_json(s: str) -> Optional[Dict[str, Any]]:
    s = s.strip()
    try:
        return json.loads(s)
    except Exception:
        # try to recover from fenced blocks
        if "```" in s:
            import re
            m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", s, re.S)
            if m:
                try:
                    return json.loads(m.group(1))
                except Exception:
                    return None
    return None