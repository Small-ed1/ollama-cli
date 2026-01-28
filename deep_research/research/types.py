from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional, Literal


Kind = Literal["web", "kiwix"]

class Reliability(str, Enum):
    PRIMARY = "primary"
    SECONDARY = "secondary"
    COMMUNITY = "community"
    UNKNOWN = "unknown"
NoteTag = Literal["definition", "howto", "number", "warning", "timeline", "context"]
ClaimStatus = Literal["supported", "disputed", "uncertain"]
Importance = Literal["core", "key", "minor"]


@dataclass
class SourceRef:
    # For web: url is set.
    url: Optional[str] = None
    # For kiwix: zim + title/id is set.
    zim: Optional[str] = None
    article_id: Optional[str] = None
    title: Optional[str] = None


@dataclass
class Source:
    source_id: str
    kind: Kind
    title: str
    publisher: str
    ref: SourceRef
    snippet: str = ""
    published_at: Optional[str] = None
    retrieved_at: str = ""
    reliability_tag: Reliability = Reliability.UNKNOWN
    score: float = 0.0

    # Filled after open/fetch
    content_text: Optional[str] = None
    content_hash: Optional[str] = None


@dataclass
class Note:
    note_id: str
    source_id: str
    quote: str
    paraphrase: str
    tag: NoteTag
    entities: List[str] = field(default_factory=list)
    units: Optional[str] = None


@dataclass
class Claim:
    claim_id: str
    text: str
    note_ids: List[str]
    source_ids: List[str]
    status: ClaimStatus
    confidence: float
    importance: Importance


@dataclass
class Conflict:
    conflict_id: str
    topic: str
    claim_ids: List[str]
    reason: str
    resolution: Optional[str] = None


@dataclass
class ResearchPlan:
    subquestions: List[str]
    kiwix_queries: List[str]
    searx_queries: List[str]
    definition_of_done: List[str]
    conflict_watch: List[str]


@dataclass
class Budgets:
    time_limit_s: int
    max_iterations: int
    max_searx_queries: int
    max_kiwix_queries: int
    max_web_fetches: int
    max_kiwix_opens: int
    max_sources_opened: int
    max_sources_cited: int
    min_source_diversity: int


@dataclass
class Parameters:
    # Your locked defaults
    mode: Literal["balanced"] = "balanced"
    citation_style: Literal["inline_plus_list"] = "inline_plus_list"
    conflict_policy: Literal["resolve_if_possible"] = "resolve_if_possible"
    archwiki_priority: bool = True

    # Behavior knobs
    offline_weight: float = 0.5
    recency_days: Optional[int] = 180
    domain_allowlist: List[str] = field(default_factory=list)
    domain_blocklist: List[str] = field(default_factory=list)
    searx_engines: List[str] = field(default_factory=list)
    zim_targets: List[str] = field(default_factory=list)

    citation_strictness: Literal["low", "med", "high"] = "med"
    min_citations_per_key_claim: int = 2
    numbers_need_two_sources: bool = True

    verbosity: Literal["short", "normal", "long"] = "normal"
    include_uncertainty: bool = True
    include_followups: bool = True


@dataclass
class ResearchState:
    query_original: str
    query_clarified: str
    parameters: Parameters
    budgets: Budgets
    plan: Optional[ResearchPlan] = None

    sources: Dict[str, Source] = field(default_factory=dict)
    notes: Dict[str, Note] = field(default_factory=dict)
    claims: Dict[str, Claim] = field(default_factory=dict)
    conflicts: Dict[str, Conflict] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)

    opened_sources: int = 0
    web_fetches: int = 0
    kiwix_opens: int = 0
    searx_queries: int = 0
    kiwix_queries: int = 0