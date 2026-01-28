from __future__ import annotations

import hashlib
import re
from typing import Optional, Tuple
import requests

from .cache import DiskCache


_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def fetch_text(url: str, cache: Optional[DiskCache] = None, ttl_s: int = 86400) -> Tuple[str, str]:
    """
    Returns (text, content_hash).
    Basic HTML tag strip. If you want high quality extraction, swap this
    for readability-lxml / trafilatura etc. (extra deps).
    """
    cache = cache or DiskCache()
    cache_key = f"fetch:{url}"
    cached = cache.get(cache_key, ttl_s=ttl_s)
    if cached:
        return cached["text"], cached["hash"]

    r = requests.get(url, timeout=25, headers={"User-Agent": "deep-research/1.0"})
    r.raise_for_status()
    raw = r.text

    # crude extraction
    text = _TAG_RE.sub(" ", raw)
    text = _WS_RE.sub(" ", text).strip()

    h = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
    cache.set(cache_key, {"text": text, "hash": h})
    return text, h