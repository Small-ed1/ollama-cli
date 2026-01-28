from __future__ import annotations

from typing import Dict, List, Tuple
from .types import Source


def score_sources(sources: List[Source], query: str, diversity_seen: Dict[str, int]) -> None:
    """
    Simple scoring:
      - relevance: keyword overlap with title/snippet
      - reliability: primary > secondary > unknown > community
      - diversity bonus: prefer new publishers
    """
    q = _tokens(query)

    for s in sources:
        text = f"{s.title} {s.snippet}".lower()
        overlap = sum(1 for t in q if t in text)
        rel = _rel_score(s.reliability_tag)
        div_bonus = 0.3 if diversity_seen.get(s.publisher, 0) == 0 else 0.0
        s.score = overlap * 0.6 + rel * 1.0 + div_bonus


def select_top(
    sources: List[Source],
    max_take: int,
    min_diversity: int,
) -> List[Source]:
    """
    Select by score, but enforce minimum publisher diversity if possible.
    """
    sorted_sources = sorted(sources, key=lambda s: s.score, reverse=True)
    chosen: List[Source] = []
    seen_pub: set[str] = set()

    # First pass: take high-score, prefer new publishers until diversity met
    for s in sorted_sources:
        if len(chosen) >= max_take:
            break
        if len(seen_pub) < min_diversity:
            if s.publisher in seen_pub:
                continue
        chosen.append(s)
        seen_pub.add(s.publisher)

    # Second pass: fill remaining slots ignoring diversity
    if len(chosen) < max_take:
        for s in sorted_sources:
            if len(chosen) >= max_take:
                break
            if s in chosen:
                continue
            chosen.append(s)

    return chosen


def _tokens(s: str) -> List[str]:
    import re
    toks = re.findall(r"[a-z0-9]+", s.lower())
    # drop tiny tokens
    return [t for t in toks if len(t) >= 3]


def _rel_score(tag: str) -> float:
    return {
        "primary": 2.0,
        "secondary": 1.2,
        "unknown": 0.6,
        "community": 0.3,
    }.get(tag, 0.6)