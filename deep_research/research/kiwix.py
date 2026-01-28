from __future__ import annotations

import hashlib
import time
from typing import Any, Dict, List, Optional
import requests

from .cache import DiskCache
from .types import Source, SourceRef, Reliability


def _stable_id(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:16]


class KiwixHTTP:
    """
    Kiwix server setups differ. This adapter is configurable.

    Config keys:
      base_url: http://127.0.0.1:8181
      endpoints.search: "/search"
      endpoints.open: "/content"

    We attempt common patterns:
      Search:
        GET {base}{search}?q=...&format=json&zim=...
        GET {base}{search}?pattern=...&format=json&zim=...
      Open:
        GET {base}{open}/{zim}/{title}
        GET {base}{open}?zim=...&title=...
    """
    def __init__(self, base_url: str, endpoints: Dict[str, str], cache: Optional[DiskCache] = None):
        self.base_url = base_url.rstrip("/")
        self.search_ep = endpoints.get("search", "/search")
        self.open_ep = endpoints.get("open", "/content")
        self.cache = cache or DiskCache()

    def search(self, query: str, zim: str, max_hits: int = 10, ttl_s: int = 86400) -> List[Source]:
        cache_key = f"kiwix_search:{zim}:{query}:{max_hits}"
        cached = self.cache.get(cache_key, ttl_s=ttl_s)
        if cached is None:
            data = self._try_search(query, zim)
            self.cache.set(cache_key, data)
        else:
            data = cached

        # Check for HTML response error - degrade gracefully
        if isinstance(data, dict) and "_error" in data:
            return []
        
        hits = _normalize_hits(data)[:max_hits]
        out: List[Source] = []
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

        for h in hits:
            title = h.get("title") or h.get("label") or "Unknown"
            article_id = h.get("id") or h.get("article_id")
            snippet = h.get("snippet") or h.get("description") or ""
            sid = _stable_id(f"kiwix:{zim}:{article_id or title}")

            rel_tag = Reliability.PRIMARY if "arch" in zim.lower() else Reliability.SECONDARY

            out.append(
                Source(
                    source_id=sid,
                    kind="kiwix",
                    title=title,
                    publisher=f"Kiwix:{zim}",
                    ref=SourceRef(zim=zim, article_id=str(article_id) if article_id else None, title=title),
                    snippet=snippet,
                    published_at=h.get("date") or None,
                    retrieved_at=now,
                    reliability_tag=rel_tag,
                    score=0.0,
                )
            )
        return out

    def open(self, zim: str, title: Optional[str] = None, article_id: Optional[str] = None, ttl_s: int = 7 * 86400) -> str:
        key = f"kiwix_open:{zim}:{article_id or ''}:{title or ''}"
        cached = self.cache.get(key, ttl_s=ttl_s)
        if cached is not None:
            return str(cached)

        text = self._try_open(zim, title=title, article_id=article_id)
        self.cache.set(key, text)
        return text

    def _try_search(self, query: str, zim: str) -> Any:
        url = f"{self.base_url}{self.search_ep}"

        # Try q=
        for params in (
            {"q": query, "zim": zim, "format": "json"},
            {"pattern": query, "zim": zim, "format": "json"},
            {"q": query, "format": "json"},  # sometimes zim selection is server-side
            {"pattern": query, "format": "json"},
        ):
            try:
                r = requests.get(url, params=params, timeout=20)
                r.raise_for_status()
                txt = r.text.lstrip()
                
                # Check for HTML response (endpoint mismatch)
                if txt.startswith("<!doctype") or txt.startswith("<html"):
                    return {"_error": "Kiwix search returned HTML; check endpoints/search params", "raw": txt[:500]}
                
                # If JSON works, return it
                ct = r.headers.get("content-type", "").lower()
                if "json" in ct:
                    return r.json()
                # Some servers return JSON with wrong content-type
                try:
                    return r.json()
                except Exception:
                    # Fallback: return text, caller will normalize to zero hits
                    return {"raw": txt}
            except Exception:
                continue

        # Final fallback
        return {"results": []}

    def _try_open(self, zim: str, title: Optional[str], article_id: Optional[str]) -> str:
        # pattern 1: /content/{zim}/{title}
        if title:
            url1 = f"{self.base_url}{self.open_ep}/{zim}/{_url_escape(title)}"
            try:
                r = requests.get(url1, timeout=25)
                r.raise_for_status()
                return _strip_html(r.text)
            except Exception:
                pass

        # pattern 2: /content?zim=...&title=...
        if title:
            url2 = f"{self.base_url}{self.open_ep}"
            try:
                r = requests.get(url2, params={"zim": zim, "title": title}, timeout=25)
                r.raise_for_status()
                return _strip_html(r.text)
            except Exception:
                pass

        # pattern 3: /content?zim=...&id=...
        if article_id:
            url3 = f"{self.base_url}{self.open_ep}"
            try:
                r = requests.get(url3, params={"zim": zim, "id": article_id}, timeout=25)
                r.raise_for_status()
                return _strip_html(r.text)
            except Exception:
                pass

        raise RuntimeError(
            f"Kiwix open failed. Check config endpoints for your kiwix-serve. "
            f"Tried zim={zim}, title={title}, article_id={article_id}."
        )


def _normalize_hits(data: Any) -> List[Dict[str, Any]]:
    if isinstance(data, dict):
        for k in ("results", "hits", "items"):
            v = data.get(k)
            if isinstance(v, list):
                return v
    return []


def _strip_html(s: str) -> str:
    import re
    s = re.sub(r"<[^>]+>", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _url_escape(s: str) -> str:
    from urllib.parse import quote
    return quote(s, safe="" )