"""Kiwix tools for offline content access.

This module provides tools for accessing offline content via Kiwix ZIM files,
using text_extract utilities to avoid circular dependencies with WebTools.
"""

import json
import time
from typing import Any, Dict, List, Optional, Tuple
import xml.etree.ElementTree as ET

import requests  # type: ignore

from ..config import DEFAULT_KIWIX_URL, DEFAULT_KIWIX_SEARCH_COUNT, DEFAULT_KIWIX_MAX_CHARS
from ..errors import ToolTimeoutError
from ..text_extract import html_to_text, clean_ws
from .core import KiwixToolError, SearchResult


class KiwixTools:
    """Kiwix integration via kiwix-serve HTTP.
    
    Provides access to offline content in ZIM files through kiwix-serve.
    """
    
    def __init__(self, kiwix_url: str = DEFAULT_KIWIX_URL, timeout: int = 10,
                 min_delay_s: float = 0.2, cache_minutes: int = 30):
        """Initialize Kiwix tools.
        
        Args:
            kiwix_url: Base URL for kiwix-serve instance
            timeout: Request timeout in seconds
            min_delay_s: Minimum delay between requests (rate limiting)
            cache_minutes: Cache duration for requests
        """
        self.kiwix_url = kiwix_url.rstrip("/")
        self.timeout = timeout
        self.min_delay_s = min_delay_s
        self._last_req_at = 0.0
        self._cache_minutes = cache_minutes
        self._cache: Dict[str, Tuple[float, Any]] = {}
        
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "ollama-cli-kiwix/1.0"})
    
    def _rate_limit(self):
        """Apply rate limiting between requests."""
        dt = time.time() - self._last_req_at
        if dt < self.min_delay_s:
            time.sleep(self.min_delay_s - dt)
        self._last_req_at = time.time()
    
    def _cache_key(self, endpoint: str, params: Dict[str, Any]) -> str:
        """Generate cache key for request."""
        key = f"{endpoint}:{json.dumps(params, sort_keys=True)}"
        import hashlib
        return hashlib.md5(key.encode()).hexdigest()
    
    def _get_cached(self, cache_key: str) -> Optional[Dict[str, Any]]:
        """Get cached response if valid."""
        if cache_key in self._cache:
            timestamp, data = self._cache[cache_key]
            if time.time() - timestamp < self._cache_minutes * 60:
                return data
            else:
                del self._cache[cache_key]
        return None
    
    def _set_cached(self, cache_key: str, data: Dict[str, Any]):
        """Cache response with timestamp."""
        self._cache[cache_key] = (time.time(), data)
    
    def suggest(self, zim: str, term: str, count: int = 8) -> Dict[str, Any]:
        """Get suggestions for content completion.
        
        Args:
            zim: ZIM file name
            term: Term to complete
            count: Number of suggestions
            
        Returns:
            Suggestions response from Kiwix
        """
        self._rate_limit()
        if not zim or not term:
            raise KiwixToolError("zim and term are required")
        
        cache_key = self._cache_key("suggest", {"zim": zim, "term": term, "count": count})
        cached = self._get_cached(cache_key)
        if cached:
            return cached
        
        try:
            response = self.session.get(
                f"{self.kiwix_url}/suggest",
                params={"content": zim, "term": term, "count": count},  # type: ignore[arg-type]
                timeout=self.timeout,
            )
            response.raise_for_status()
            data = response.json()
            self._set_cached(cache_key, data)
            return data
        except requests.Timeout as e:
            raise ToolTimeoutError(f"kiwix suggest timed out: {e}") from e
        except requests.RequestException as e:
            raise KiwixToolError(f"suggest failed: {e}") from e
        except ValueError as e:
            raise KiwixToolError(f"suggest returned invalid JSON: {e}") from e
    
    def search_xml(self, query: str, zim: str, count: int = 8, start: int = 0) -> List[SearchResult]:
        """Search ZIM content using XML endpoint.
        
        Args:
            query: Search query
            zim: ZIM file name
            count: Number of results
            start: Start index for pagination
            
        Returns:
            List of search results
        """
        self._rate_limit()
        if not query.strip():
            raise KiwixToolError("query cannot be empty")
        if not zim:
            raise KiwixToolError("zim is required")
        
        cache_key = self._cache_key("search", {"query": query, "zim": zim, "count": count, "start": start})
        cached = self._get_cached(cache_key)
        if cached:
            return [SearchResult(**r) for r in cached.get("results", [])]
        
        try:
            response = self.session.get(
                f"{self.kiwix_url}/search",
                params={
                    "pattern": query,
                    "content": zim,
                    "format": "xml",
                    "pageLength": count,
                    "start": start,
                },  # type: ignore[arg-type]
                timeout=self.timeout,
            )
            response.raise_for_status()
            
            # Parse XML response
            root = ET.fromstring(response.text)
            results = []
            
            for item in root.findall(".//item"):
                title = item.findtext("title", "")
                url = item.findtext("url", "")
                snippet = item.findtext("snippet", "")
                
                if title and url:
                    results.append(SearchResult(
                        title=title,
                        url=url,
                        snippet=self._clean_ws(snippet)[:350],
                    ))
            
            self._set_cached(cache_key, {"results": [r.__dict__ for r in results]})
            return results
            
        except requests.Timeout as e:
            raise ToolTimeoutError(f"kiwix search timed out: {e}") from e
        except requests.RequestException as e:
            raise KiwixToolError(f"search request failed: {e}") from e
        except ET.ParseError as e:
            raise KiwixToolError(f"invalid XML response: {e}") from e
    
    def open_raw(self, zim: str, path: str, max_chars: int = 12000) -> Dict[str, Any]:
        """Fetch raw content from ZIM file.
        
        Args:
            zim: ZIM file name
            path: Content path within ZIM
            max_chars: Maximum characters to extract
            
        Returns:
            Dictionary with content and metadata
        """
        self._rate_limit()
        if not zim or not path:
            raise KiwixToolError("zim and path are required")
        
        cache_key = self._cache_key("open", {"zim": zim, "path": path, "max_chars": max_chars})
        cached = self._get_cached(cache_key)
        if cached:
            return cached
        
        try:
            response = self.session.get(
                f"{self.kiwix_url}/content",
                params={"content": zim, "url": path},
                timeout=self.timeout,
            )
            response.raise_for_status()
            
            # Get content and metadata
            body = response.text
            content_type = response.headers.get('content-type', 'text/plain')
            
            # Extract readable text if HTML
            text = body
            if "text/html" in content_type.lower():
                # Use text_extract utilities to avoid circular dependency
                text = html_to_text(body)
            
            # Truncate if necessary
            truncated = len(text) > max_chars
            if truncated:
                text = text[:max_chars]
            
            result = {
                "zim": zim,
                "path": path,
                "content_type": content_type,
                "content": text,
                "size": len(text),
                "truncated": truncated,
            }
            
            self._set_cached(cache_key, result)
            return result
            
        except requests.Timeout as e:
            raise ToolTimeoutError(f"kiwix open timed out: {e}") from e
        except requests.RequestException as e:
            raise KiwixToolError(f"open request failed: {e}") from e
    
    def list_zims(self, zim_dir: str = "/mnt/zim/zims") -> List[Dict[str, Any]]:
        """List available ZIM files.
        
        Args:
            zim_dir: Directory containing ZIM files
            
        Returns:
            List of ZIM file information
        """
        # Note: This is a placeholder implementation
        # In a real scenario, you'd scan the directory or query kiwix-serve
        return [
            {"name": "wikipedia_en_all_maxi_2024-10", "title": "English Wikipedia", "size": "90GB"},
            {"name": "stackexchange_en_all", "title": "Stack Exchange", "size": "30GB"},
        ]
    
    def _clean_ws(self, s: str) -> str:
        """Clean whitespace (alias for text_extract.clean_ws)."""
        return clean_ws(s)


def tool_kiwix_search(
    kiwix_tools: KiwixTools,
    query: str,
    zim: str,
    count: int = DEFAULT_KIWIX_SEARCH_COUNT,
    start: int = 0,
) -> str:
    """Tool wrapper for Kiwix search.
    
    Args:
        kiwix_tools: KiwixTools instance
        query: Search query
        zim: ZIM file name
        count: Number of results (default 8)
        start: Start index for pagination (default 0)
        
    Returns:
        JSON string with search results
    """
    results = kiwix_tools.search_xml(query, zim, count, start)

    return json.dumps(
        {
            "query": query,
            "zim": zim,
            "results": [result.__dict__ for result in results],
            "count": len(results),
            "start": start,
        },
        indent=2,
        ensure_ascii=False,
    )


def tool_kiwix_open(
    kiwix_tools: KiwixTools,
    zim: str,
    path: str,
    max_chars: int = DEFAULT_KIWIX_MAX_CHARS,
) -> str:
    """Tool wrapper for opening Kiwix content.
    
    Args:
        kiwix_tools: KiwixTools instance
        zim: ZIM file name
        path: Content path within ZIM
        max_chars: Maximum characters to extract (default 12000)
        
    Returns:
        JSON string with content
    """
    result = kiwix_tools.open_raw(zim, path, max_chars)
    return json.dumps(result, indent=2, ensure_ascii=False)


def tool_kiwix_suggest(kiwix_tools: KiwixTools, zim: str, term: str, count: int = 8) -> str:
    """Tool wrapper for Kiwix suggestions.
    
    Args:
        kiwix_tools: KiwixTools instance
        zim: ZIM file name
        term: Term to complete
        count: Number of suggestions (default 8)
        
    Returns:
        JSON string with suggestions
    """
    result = kiwix_tools.suggest(zim, term, count)
    return json.dumps(result, indent=2, ensure_ascii=False)


def tool_kiwix_list_zims(kiwix_tools: KiwixTools, zim_dir: str = "/mnt/zim/zims") -> str:
    """Tool wrapper for listing ZIM files.
    
    Args:
        kiwix_tools: KiwixTools instance
        zim_dir: Directory containing ZIM files (default '/mnt/zim/zims')
        
    Returns:
        JSON string with ZIM file list
    """
    results = kiwix_tools.list_zims(zim_dir)
    return json.dumps(
        {
            "zim_dir": zim_dir,
            "zims": results,
            "count": len(results),
        },
        indent=2,
        ensure_ascii=False,
    )
