from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

from .types import Budgets, Parameters


PRESETS: Dict[str, Budgets] = {
    "tiny": Budgets(
        time_limit_s=30,
        max_iterations=2,
        max_searx_queries=2,
        max_kiwix_queries=2,
        max_web_fetches=3,
        max_kiwix_opens=2,
        max_sources_opened=6,
        max_sources_cited=5,
        min_source_diversity=2,
    ),
    "standard": Budgets(
        time_limit_s=120,
        max_iterations=6,
        max_searx_queries=6,
        max_kiwix_queries=4,
        max_web_fetches=10,
        max_kiwix_opens=6,
        max_sources_opened=18,
        max_sources_cited=10,
        min_source_diversity=4,
    ),
    "deep": Budgets(
        time_limit_s=300,
        max_iterations=12,
        max_searx_queries=15,
        max_kiwix_queries=10,
        max_web_fetches=40,
        max_kiwix_opens=15,
        max_sources_opened=60,
        max_sources_cited=20,   # anti-drowning cap
        min_source_diversity=6,
    ),
}


@dataclass
class AppConfig:
    raw: Dict[str, Any]

    @property
    def ollama(self) -> Dict[str, Any]:
        return self.raw.get("ollama", {})

    @property
    def searxng(self) -> Dict[str, Any]:
        return self.raw.get("searxng", {})

    @property
    def kiwix(self) -> Dict[str, Any]:
        return self.raw.get("kiwix", {})

    @property
    def defaults(self) -> Dict[str, Any]:
        return self.raw.get("defaults", {})


def load_config(path: str) -> AppConfig:
    p = Path(path).expanduser()
    data = json.loads(p.read_text(encoding="utf-8"))
    return AppConfig(raw=data)


def default_parameters(cfg: AppConfig) -> Parameters:
    d = cfg.defaults
    return Parameters(
        mode="balanced",
        citation_style="inline_plus_list",
        conflict_policy="resolve_if_possible",
        archwiki_priority=bool(d.get("archwiki_priority", True)),
        zim_targets=cfg.kiwix.get("zim_targets", []),
        searx_engines=cfg.searxng.get("default_engines", []),
        recency_days=180,
        citation_strictness="med",
        min_citations_per_key_claim=2,
        numbers_need_two_sources=True,
        verbosity="normal",
        include_uncertainty=True,
        include_followups=True,
    )