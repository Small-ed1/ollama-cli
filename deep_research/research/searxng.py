from __future__ import annotations

import hashlib
import time
from typing import Any, Dict, List, Optional, Tuple
import requests

from .cache import DiskCache
from .types import Source, SourceRef, Reliability


def _stable_id(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:16]


class SearxNG:
    def __init__(self, base_url: str, cache: Optional[DiskCache] = None, safesearch: int = 1):
        self.base_url = base_url.rstrip("/")
        self.cache = cache or DiskCache()
        self.safesearch = safesearch

    def search(
        self,
        query: str,
        lang: str = "en",
        engines: Optional[List[str]] = None,
        time_range: Optional[str] = None,
        domains_allow: Optional[List[str]] = None,
        domains_block: Optional[List[str]] = None,
        max_results: int = 20,
        ttl_s: int = 3600,
    ) -> List[Source]:
        """
        Uses SearxNG JSON format if available.
        Many SearxNG instances support:
          /search?q=...&format=json
        """
        engines = engines or []
        domains_allow = domains_allow or []
        domains_block = domains_block or []

        cache_key = f"searx:{query}|{lang}|{','.join(engines)}|{time_range}|{','.join(domains_allow)}|{','.join(domains_block)}|{max_results}"
        cached = self.cache.get(cache_key, ttl_s=ttl_s)
        if cached is None:
            params: dict[str, str | int] = {
                "q": query,
                "format": "json",
                "language": lang,
                "safesearch": int(self.safesearch),
            }
            if engines:
                params["engines"] = ",".join(engines)
            if time_range:
                params["time_range"] = time_range

            r = requests.get(f"{self.base_url}/search", params=params, timeout=20)
            r.raise_for_status()
            data = r.json()
            self.cache.set(cache_key, data)
        else:
            data = cached

        results = data.get("results", [])[:max_results]
        out: List[Source] = []
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

        for item in results:
            url = item.get("url") or ""
            title = item.get("title") or url
            content = item.get("content") or item.get("snippet") or ""
            pub = _publisher_from_url(url)

            if domains_allow and pub not in domains_allow:
                continue
            if domains_block and pub in domains_block:
                continue

            sid = _stable_id(f"web:{url}")
            out.append(
                Source(
                    source_id=sid,
                    kind="web",
                    title=title,
                    publisher=pub,
                    ref=SourceRef(url=url),
                    snippet=content,
                    published_at=item.get("publishedDate") or item.get("published_at"),
                    retrieved_at=now,
                    reliability_tag=_default_reliability(pub),
                    score=0.0,
                )
            )

        return out


def _publisher_from_url(url: str) -> str:
    try:
        from urllib.parse import urlparse
        host = urlparse(url).netloc.lower()
        if host.startswith("www."):
            host = host[4:]
        return host or "unknown"
    except Exception:
        return "unknown"


def _default_reliability(publisher: str) -> Reliability:
    # Lightweight heuristic. You can expand this with allowlists.
    if publisher in {"archlinux.org", "wiki.archlinux.org"}:
        return Reliability.PRIMARY
    if publisher.endswith(".edu") or publisher.endswith(".gov"):
        return Reliability.PRIMARY
    if publisher in {"wikipedia.org"}:
        return Reliability.SECONDARY
    return Reliability.UNKNOWN