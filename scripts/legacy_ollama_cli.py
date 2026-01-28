#!/usr/bin/env python3
import argparse
import json
import os
import re
import sys
import datetime
import time
import subprocess
import uuid
import xml.etree.ElementTree as ET
from urllib.parse import urlparse, urlencode
from dataclasses import dataclass, asdict
from typing import Any, Dict, Generator, List, Optional, Tuple
from argparse import Namespace

import requests

# Optional web dependencies - install on first use with graceful fallback
_web_deps_installed = False
_trafilatura: Any = None
_beautifulsoup: Any = None

def _ensure_web_deps():
    global _web_deps_installed, _trafilatura, _beautifulsoup
    if _web_deps_installed:
        return True
    
    # Check if dependencies are available by importing actual modules
    missing = []
    try:
        import trafilatura  # type: ignore[import-not-found]
    except ImportError:
        missing.append('trafilatura')
    
    try:
        import bs4
    except ImportError:
        missing.append('beautifulsoup4')  # Package name, not import name
    
    if missing:
        # Also install readability-lxml for better extraction
        all_deps = missing + ['readability-lxml']
        try:
            subprocess.run([sys.executable, '-m', 'pip', 'install'] + all_deps, 
                          check=True, capture_output=True)
        except (subprocess.CalledProcessError, FileNotFoundError):
            return False
    
    # Try importing again after installation
    try:
        import trafilatura  # type: ignore[import-not-found]
        import bs4
        _trafilatura = trafilatura
        _beautifulsoup = bs4
        _web_deps_installed = True
        return True
    except ImportError:
        return False


DEFAULT_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")


class OllamaAPIError(RuntimeError):
    pass


class OllamaClient:
    def __init__(self, base_url: str = DEFAULT_BASE_URL, timeout: int = 60):
        # Ollama API lives under /api
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

        # Local API doesn't need auth; cloud can use Bearer API key :contentReference[oaicite:2]{index=2}
        api_key = os.getenv("OLLAMA_API_KEY")
        self.headers = {"Content-Type": "application/json"}
        if api_key:
            self.headers["Authorization"] = f"Bearer {api_key}"

    def _url(self, path: str) -> str:
        path = path.lstrip("/")
        return f"{self.base_url}/api/{path}"

    def _post_stream(
        self, path: str, payload: Dict[str, Any]
    ) -> Generator[Dict[str, Any], None, None]:
        try:
            r = requests.post(
                self._url(path),
                headers=self.headers,
                json=payload,
                stream=True,
                timeout=self.timeout,
            )
            r.raise_for_status()
        except requests.RequestException as e:
            raise OllamaAPIError(str(e)) from e

        for line in r.iter_lines(decode_unicode=True):
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                # Ollama should be newline-delimited JSON; ignore garbage safely.
                continue

    def _post_json(self, path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        try:
            r = requests.post(
                self._url(path),
                headers=self.headers,
                json=payload,
                timeout=self.timeout,
            )
            r.raise_for_status()
            return r.json()
        except requests.RequestException as e:
            raise OllamaAPIError(str(e)) from e

    def tags(self) -> Dict[str, Any]:
        # GET /api/tags returns { "models": [...] } :contentReference[oaicite:3]{index=3}
        try:
            r = requests.get(self._url("tags"), headers=self.headers, timeout=self.timeout)
            r.raise_for_status()
            return r.json()
        except requests.RequestException as e:
            raise OllamaAPIError(str(e)) from e

    def pull(self, model: str, stream: bool = True) -> Generator[Dict[str, Any], None, None]:
        # POST /api/pull { "model": "gemma3" } :contentReference[oaicite:4]{index=4}
        payload = {"model": model, "stream": stream}
        if stream:
            yield from self._post_stream("pull", payload)
        else:
            yield self._post_json("pull", payload)

    def generate(
        self,
        model: str,
        prompt: str,
        system: Optional[str] = None,
        stream: bool = True,
        options: Optional[Dict[str, Any]] = None,
        think: Optional[Any] = None,
    ) -> Generator[Dict[str, Any], None, None]:
        # POST /api/generate {model,prompt,...} :contentReference[oaicite:5]{index=5}
        payload: Dict[str, Any] = {"model": model, "prompt": prompt, "stream": stream}
        if system:
            payload["system"] = system
        if options:
            payload["options"] = options
        if think is not None:
            payload["think"] = think

        if stream:
            yield from self._post_stream("generate", payload)
        else:
            yield self._post_json("generate", payload)

    def chat(
        self,
        model: str,
        messages: List[Dict[str, Any]],
        system: Optional[str] = None,
        stream: bool = True,
        options: Optional[Dict[str, Any]] = None,
        think: Optional[Any] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
    ) -> Generator[Dict[str, Any], None, None]:
        # POST /api/chat {model,messages,...} :contentReference[oaicite:6]{index=6}
        payload: Dict[str, Any] = {"model": model, "messages": messages, "stream": stream}
        if system:
            payload["system"] = system
        if options:
            payload["options"] = options
        if think is not None:
            payload["think"] = think
        if tools:
            payload["tools"] = tools

        if stream:
            yield from self._post_stream("chat", payload)
        else:
            yield self._post_json("chat", payload)

#
# -------------------------
# Simple tool-calling layer
# -------------------------
#

class ToolError(RuntimeError):
    pass


class WebToolError(ToolError):
    pass


class KiwixToolError(ToolError):
    pass


@dataclass
class SearchResult:
    rank: int
    title: str
    url: str
    snippet: str
    source: str = ""
    published: Optional[str] = None


class WebTools:
    """
    Web search and content extraction tools.
    - Search returns multiple URLs + snippets
    - Open fetches and extracts readable text
    - Simple caching + rate limiting
    """

    def __init__(
        self,
        searxng_url: Optional[str] = None,
        timeout: int = 20,
        min_delay_s: float = 0.7,
        cache_minutes: int = 10,
    ):
        if searxng_url is None:
            searxng_url = os.getenv("SEARXNG_URL", "http://localhost:8080/search")
        self.searxng_url = searxng_url.rstrip("/")
        self.timeout = timeout
        self.min_delay_s = min_delay_s
        self._last_req_at = 0.0
        self._cache_minutes = cache_minutes
        self._cache: Dict[str, Tuple[float, Dict[str, Any]]] = {}  # url -> (ts, payload)
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "ollama-cli-webtool/1.0 (+https://example.local)"
        })

    def _rate_limit(self):
        dt = time.time() - self._last_req_at
        if dt < self.min_delay_s:
            time.sleep(self.min_delay_s - dt)
        self._last_req_at = time.time()

    def web_search(self, query: str, count: int = 8, recency_days: int = 365,
                   site: str = "", include_domains: Optional[List[str]] = None,
                   exclude_domains: Optional[List[str]] = None, safe: bool = True) -> Dict[str, Any]:
        self._rate_limit()

        if not query.strip():
            raise WebToolError("query is required")

        # SearxNG supports many params; keep it clean and stable.
        search_query = query if not site else f"site:{site} {query}"
        params = {
            "q": search_query,
            "format": "json",
            "safesearch": 2 if safe else 0,
        }
        # SearxNG Search API supports time_range: day/month/year only
        # Map recency_days to appropriate buckets, omit for >365
        if recency_days <= 1:
            params["time_range"] = "day"
        elif recency_days <= 31:
            params["time_range"] = "month"
        elif recency_days <= 365:
            params["time_range"] = "year"
        # >365: omit time_range entirely (implementation choice)

        try:
            r = self.session.get(self.searxng_url, params=params, timeout=self.timeout)
            r.raise_for_status()
            data = r.json()
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 403:
                raise WebToolError(
                    "403 Forbidden: JSON output likely disabled. "
                    "Enable search.formats: [html, json] in settings.yml"
                ) from e
            raise WebToolError(f"search failed: HTTP {e.response.status_code}") from e
        except Exception as e:
            raise WebToolError(f"search failed: {e}") from e

        results: List[SearchResult] = []
        raw = data.get("results", []) or []
        for item in raw:
            url = item.get("url") or ""
            title = item.get("title") or ""
            snippet = item.get("content") or item.get("snippet") or ""
            source = item.get("engine") or item.get("source") or ""

            if not url:
                continue

            host = urlparse(url).netloc.lower()
            if include_domains and not any(host.endswith(d.lower()) for d in include_domains):
                continue
            if exclude_domains and any(host.endswith(d.lower()) for d in exclude_domains):
                continue

            results.append(SearchResult(
                rank=len(results) + 1,
                title=title.strip(),
                url=url.strip(),
                snippet=self._clean_ws(snippet)[:350],
                source=source,
                published=item.get("publishedDate") or item.get("published") or None,
            ))
            if len(results) >= count:
                break

        return {
            "query": query,
            "count": len(results),
            "results": [asdict(x) for x in results],
        }

    def web_open(self, url: str, mode: str = "auto", max_chars: int = 12000,
                 include_html: bool = False, follow_links: bool = False, link_limit: int = 3) -> Dict[str, Any]:
        if not url:
            raise WebToolError("url is required")
        if max_chars < 500 or max_chars > 200_000:
            raise WebToolError("max_chars out of range")

        # cache: configurable minutes
        now = time.time()
        cached = self._cache.get(url)
        if cached and (now - cached[0]) < (self._cache_minutes * 60):
            return cached[1]

        self._rate_limit()
        try:
            r = self.session.get(url, timeout=self.timeout)
            r.raise_for_status()
            html = r.text
        except Exception as e:
            raise WebToolError(f"open failed: {e}") from e

        content_type = r.headers.get("Content-Type", "")
        title = ""
        if "text/html" in content_type.lower():
            if not _ensure_web_deps():
                # Fallback without beautifulsoup
                title = url
            else:
                soup = _beautifulsoup.BeautifulSoup(html, "html.parser")
                title = (soup.title.string.strip() if soup.title and soup.title.string else "")

        text = ""
        extracted = None
        if mode in ("auto", "article") and "text/html" in content_type.lower():
            if _ensure_web_deps():
                extracted = _trafilatura.extract(html, include_comments=False, include_tables=True)
                text = extracted or ""
        if not text:  # fallback to raw-ish
            text = self._html_to_text(html) if "text/html" in content_type.lower() else html

        text = text.strip()
        if len(text) > max_chars:
            text = text[:max_chars] + "\n...[truncated]"

        payload: Dict[str, Any] = {
            "url": url,
            "title": title,
            "content_type": content_type,
            "text": text,
        }
        if include_html:
            payload["html"] = html[:max_chars]

        # Optional: "deep pull" a few same-site links
        if follow_links and "text/html" in content_type.lower():
            same_site = self._extract_same_site_links(url, html)
            pulled = []
            for u in same_site[: max(0, link_limit)]:
                try:
                    pulled.append(self.web_open(u, mode=mode, max_chars=max_chars, include_html=False,
                                                follow_links=False, link_limit=0))
                except Exception:
                    continue
            payload["linked_pages"] = pulled

        self._cache[url] = (now, payload)
        return payload

    def _clean_ws(self, s: str) -> str:
        return " ".join((s or "").split())

    def _html_to_text(self, html: str) -> str:
        if not _ensure_web_deps():
            return html  # Fallback to raw HTML
        soup = _beautifulsoup.BeautifulSoup(html, "html.parser")
        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()
        return self._clean_ws(soup.get_text(" "))

    def _extract_same_site_links(self, base_url: str, html: str) -> List[str]:
        if not _ensure_web_deps():
            return []
        base_host = urlparse(base_url).netloc.lower()
        soup = _beautifulsoup.BeautifulSoup(html, "html.parser")
        urls = []
        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            if href.startswith("#") or href.startswith("mailto:"):
                continue
            # very light "absolutize"
            if href.startswith("/"):
                parsed = urlparse(base_url)
                href = f"{parsed.scheme}://{parsed.netloc}{href}"
            if href.startswith("http"):
                host = urlparse(href).netloc.lower()
                if host == base_host:
                    urls.append(href)
        # de-dupe preserve order
        seen = set()
        out = []
        for u in urls:
            if u not in seen:
                seen.add(u)
                out.append(u)
        return out


class KiwixTools:
    """
    Kiwix integration via kiwix-serve HTTP.
    - search_xml: full-text search (XML) -> list of hits with paths
    - suggest: autocomplete (JSON)
    - open_raw: fetch article content
    """

    def __init__(self, kiwix_url: Optional[str] = None, timeout: int = 10, min_delay_s: float = 0.2,
                 cache_minutes: int = 30):
        if kiwix_url is None:
            kiwix_url = os.getenv("KIWIX_URL", "http://127.0.0.1:8080")
        self.kiwix_url = kiwix_url.rstrip("/")
        self.timeout = timeout
        self.min_delay_s = min_delay_s
        self._last_req_at = 0.0
        self._cache_minutes = cache_minutes
        self._cache: Dict[str, Tuple[float, Dict[str, Any]]] = {}

        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "ollama-cli-kiwix/1.0"})

    def _rate_limit(self):
        dt = time.time() - self._last_req_at
        if dt < self.min_delay_s:
            time.sleep(self.min_delay_s - dt)
        self._last_req_at = time.time()

    def suggest(self, zim: str, term: str, count: int = 8) -> Dict[str, Any]:
        self._rate_limit()
        if not zim or not term:
            raise KiwixToolError("zim and term are required")

        try:
            r = self.session.get(
                f"{self.kiwix_url}/suggest",
                params={"content": zim, "term": term, "count": count},
                timeout=self.timeout,
            )
            r.raise_for_status()
            return r.json()
        except Exception as e:
            raise KiwixToolError(f"suggest failed: {e}") from e

    def search_xml(self, query: str, zim: str, count: int = 8, start: int = 0) -> List[Dict[str, Any]]:
        """
        GET /search?pattern=...&content=ZIM&format=xml&pageLength=...&start=...
        Returns a list of {title, path, snippet}.
        """
        self._rate_limit()
        if not query.strip():
            raise KiwixToolError("query is required")
        if not zim.strip():
            raise KiwixToolError("zim is required")

        # cache (search results can be cached briefly)
        cache_key = f"search::{zim}::{query}::{count}::{start}"
        now = time.time()
        cached = self._cache.get(cache_key)
        if cached and (now - cached[0]) < (min(self._cache_minutes, 10) * 60):
            return cached[1]["items"]

        try:
            r = self.session.get(
                f"{self.kiwix_url}/search",
                params={
                    "pattern": query,
                    "content": zim,
                    "format": "xml",
                    "pageLength": count,
                    "start": start,
                },
                timeout=self.timeout,
            )
            r.raise_for_status()
            xml_text = r.text or ""
        except Exception as e:
            raise KiwixToolError(f"search failed: {e}") from e

        items = self._parse_search_xml(xml_text, zim)

        self._cache[cache_key] = (now, {"items": items})
        return items

    def open_raw(self, zim: str, path: str, max_chars: int = 12000) -> Dict[str, Any]:
        if not zim or not path:
            raise KiwixToolError("zim and path are required")
        if max_chars < 100 or max_chars > 200_000:
            raise KiwixToolError("max_chars out of range")

        cache_key = f"open::{zim}::{path}::{max_chars}"
        now = time.time()
        cached = self._cache.get(cache_key)
        if cached and (now - cached[0]) < (self._cache_minutes * 60):
            return cached[1]

        self._rate_limit()
        url = f"{self.kiwix_url}/raw/{zim}/content/{path.lstrip('/')}"
        try:
            r = self.session.get(url, timeout=self.timeout)
            r.raise_for_status()
        except Exception as e:
            raise KiwixToolError(f"open failed: {e}") from e

        content_type = r.headers.get("Content-Type", "") or ""
        body = r.text or ""

        # Reuse HTML->text extraction from WebTools if available
        text = body
        if "text/html" in content_type.lower():
            web_tools = _get_web_tools()
            if web_tools:
                try:
                    # Test if web tools dependencies are working
                    test_html = "<html><body>test</body></html>"
                    cleaned_test = web_tools._html_to_text(test_html)
                    if cleaned_test == test_html:  # Dependencies not working
                        raise Exception("Web deps not installed")
                    text = web_tools._html_to_text(body)
                except Exception:
                    # Fallback: basic HTML cleaning without web tools
                    text = re.sub(r'<[^>]+>', '', body)
                    text = " ".join(text.split())
            else:
                # Fallback: basic HTML cleaning without web tools
                text = re.sub(r'<[^>]+>', '', body)
                text = " ".join(text.split())

        text = text.strip()
        if len(text) > max_chars:
            text = text[:max_chars] + "\n...[truncated]"

        payload = {"url": url, "content_type": content_type, "text": text}
        self._cache[cache_key] = (now, payload)
        return payload

    def _parse_search_xml(self, xml_text: str, zim: str = "") -> List[Dict[str, Any]]:
        """
        Tolerant parser: kiwix-serve XML formats vary across versions.
        Tries to extract title/path/snippet from common tag/attr patterns.
        """
        out: List[Dict[str, Any]] = []
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError:
            # fall back: expose raw
            return [{"title": "", "path": "", "snippet": xml_text[:2000]}]

        # Parse RSS format with <item> elements
        for item in root.findall(".//item"):
            title = item.findtext("title", "").strip()
            link = item.findtext("link", "").strip()
            description = item.findtext("description", "").strip()
            
            # Extract path from link like /content/ZIM/Path
            path = ""
            if link:
                parts = link.split("/content/")
                if len(parts) > 1:
                    full_path = parts[1]
                    # Remove ZIM name if present at the beginning
                    path_parts = full_path.split("/", 1)
                    if len(path_parts) > 1 and path_parts[0] == zim:
                        path = path_parts[1]
                    else:
                        path = full_path

            # Clean HTML from description
            snippet = description
            if snippet:
                # Remove HTML tags
                snippet = re.sub(r'<[^>]+>', '', snippet)
                snippet = " ".join(snippet.split())

            if title or path or snippet:
                out.append({"title": title, "path": path, "snippet": snippet})

        # Fallback: try other XML structures
        if not out:
            for el in root.iter():
                tag = (el.tag or "").lower()
                if tag.endswith("result") or tag.endswith("entry") or tag.endswith("hit"):
                    title = (el.get("title") or el.findtext("title") or "").strip()
                    path = (
                        (el.get("path") or el.get("url") or el.get("id") or "").strip()
                        or (el.findtext("path") or el.findtext("url") or el.findtext("id") or "").strip()
                    )
                    snippet = (
                        (el.get("snippet") or "").strip()
                        or (el.findtext("snippet") or el.findtext("extract") or el.findtext("description") or "").strip()
                    )

                    if title or path or snippet:
                        out.append({"title": title, "path": path, "snippet": " ".join(snippet.split())})

        # If we got nothing, keep a small raw preview so the model can recover/debug
        if not out:
            out.append({"title": "", "path": "", "snippet": xml_text[:2000]})
        return out


# Global web tools instance
_web_tools = None

def _get_web_tools() -> Optional[WebTools]:
    global _web_tools
    if _web_tools is None:
        try:
            _web_tools = WebTools()
        except Exception:
            # Could be misconfigured, but don't fail entirely
            pass
    return _web_tools


# Global kiwix tools instance
_kiwix_tools = None

def _get_kiwix_tools() -> Optional[KiwixTools]:
    global _kiwix_tools
    if _kiwix_tools is None:
        try:
            _kiwix_tools = KiwixTools()
        except Exception:
            # Could be misconfigured, but don't fail entirely
            pass
    return _kiwix_tools


def _tool_schema(name: str, description: str, parameters: Dict[str, Any]) -> Dict[str, Any]:
    # Ollama tool format: {"type":"function","function":{"name":...,"description":...,"parameters":...}}
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": parameters,
        },
    }


def tool_get_time(tz: str = "local") -> str:
    """Return the current time."""
    now = datetime.datetime.now(datetime.timezone.utc)
    if tz == "utc":
        return now.strftime("%Y-%m-%d %H:%M:%S UTC")
    # local
    local = now.astimezone()
    return local.strftime("%Y-%m-%d %H:%M:%S %Z")


def tool_read_file(path: str, max_bytes: int = 8000) -> str:
    """Read a text file (small preview)."""
    if not path:
        raise ToolError("path is required")
    if max_bytes <= 0 or max_bytes > 200_000:
        raise ToolError("max_bytes must be between 1 and 200000")

    # keep it simple + safe-ish: only regular files
    if not os.path.isfile(path):
        raise ToolError("not a file")

    with open(path, "rb") as f:
        data = f.read(max_bytes + 1)
    truncated = len(data) > max_bytes
    data = data[:max_bytes]
    try:
        text = data.decode("utf-8", errors="replace")
    except Exception:
        text = repr(data)
    if truncated:
        text += "\n...[truncated]"
    return text


def tool_web_search(query: str, count: int = 8, recency_days: int = 365,
                    site: str = "", include_domains: Optional[List[str]] = None,
                    exclude_domains: Optional[List[str]] = None, safe: bool = True) -> str:
    """Search the web and return ranked URLs with snippets."""
    web_tools = _get_web_tools()
    if not web_tools:
        raise WebToolError("Web tools not available - check SearxNG configuration")
    
    try:
        result = web_tools.web_search(
            query=query,
            count=count,
            recency_days=recency_days,
            site=site,
            include_domains=include_domains,
            exclude_domains=exclude_domains,
            safe=safe
        )
        return json.dumps(result, indent=2, ensure_ascii=False)
    except Exception as e:
        raise WebToolError(f"Web search failed: {e}") from e


def tool_web_open(url: str, mode: str = "auto", max_chars: int = 12000,
                  include_html: bool = False, follow_links: bool = False, link_limit: int = 3) -> str:
    """Fetch a URL and extract clean readable content."""
    web_tools = _get_web_tools()
    if not web_tools:
        raise WebToolError("Web tools not available - check SearxNG configuration")
    
    try:
        result = web_tools.web_open(
            url=url,
            mode=mode,
            max_chars=max_chars,
            include_html=include_html,
            follow_links=follow_links,
            link_limit=link_limit
        )
        return json.dumps(result, indent=2, ensure_ascii=False)
    except Exception as e:
        raise WebToolError(f"Web open failed: {e}") from e


def tool_kiwix_search(query: str, zim: str, count: int = 8, start: int = 0) -> str:
    kt = _get_kiwix_tools()
    if not kt:
        raise KiwixToolError("Kiwix tools not available - check KIWIX_URL / kiwix-serve")
    items = kt.search_xml(query=query, zim=zim, count=count, start=start)

    # English-first ranking: prioritize exact matches, ASCII titles, no parentheses
    q = (query or "").strip().casefold()
    
    def _hit_score(title: str) -> tuple:
        t = (title or "").strip()
        tf = t.casefold()

        exact = 0 if (tf == q) else 1
        has_parens = 1 if ("(" in t and ")" in t) else 0
        non_ascii = 1 if any(ord(c) > 127 for c in t) else 0
        return (exact, has_parens, non_ascii, len(t))

    items = sorted(items, key=lambda it: _hit_score(it.get("title", "")))

    # Map into SearchResult-ish format (rank/title/url/snippet/source)
    results = []
    for i, it in enumerate(items[:count], start=1):
        results.append({
            "rank": i,
            "title": it.get("title", ""),
            "url": it.get("path", ""),  # internal path inside zim
            "snippet": (it.get("snippet", "") or "")[:350],
            "source": "kiwix",
        })

    return json.dumps({"query": query, "zim": zim, "count": len(results), "results": results},
                      indent=2, ensure_ascii=False)


def tool_kiwix_open(zim: str, path: str, max_chars: int = 12000) -> str:
    kt = _get_kiwix_tools()
    if not kt:
        raise KiwixToolError("Kiwix tools not available - check KIWIX_URL / kiwix-serve")
    payload = kt.open_raw(zim=zim, path=path, max_chars=max_chars)
    return json.dumps(payload, indent=2, ensure_ascii=False)


def tool_kiwix_suggest(zim: str, term: str, count: int = 8) -> str:
    kt = _get_kiwix_tools()
    if not kt:
        raise KiwixToolError("Kiwix tools not available - check KIWIX_URL / kiwix-serve")
    payload = kt.suggest(zim=zim, term=term, count=count)
    return json.dumps(payload, indent=2, ensure_ascii=False)


def tool_kiwix_list_zims(zim_dir: str = "/mnt/zim/zims") -> str:
    """
    List ZIM files by filename -> content name (file basename).
    This matches what kiwix-serve uses for `content=...`.
    """
    if not os.path.isdir(zim_dir):
        raise KiwixToolError(f"ZIM dir not found: {zim_dir}")

    zims = []
    for fn in sorted(os.listdir(zim_dir)):
        if fn.lower().endswith(".zim"):
            name = fn[:-4]  # strip .zim
            zims.append({"name": name, "file": os.path.join(zim_dir, fn)})

    return json.dumps({"count": len(zims), "zims": zims}, indent=2, ensure_ascii=False)


TOOL_SPECS: List[Dict[str, Any]] = [
    _tool_schema(
        name="get_time",
        description="Get the current time (local or utc).",
        parameters={
            "type": "object",
            "properties": {
                "tz": {"type": "string", "description": "Timezone: 'local' or 'utc'", "enum": ["local", "utc"]},
            },
        },
    ),
    _tool_schema(
        name="read_file",
        description="Read a local text file and return a small preview.",
        parameters={
            "type": "object",
            "required": ["path"],
            "properties": {
                "path": {"type": "string", "description": "Path to a local file"},
                "max_bytes": {"type": "integer", "description": "Max bytes to read (default 8000)"},
            },
        },
    ),
    _tool_schema(
        name="web_search",
        description="Search the web and return multiple ranked URLs with snippets/metadata.",
        parameters={
            "type": "object",
            "required": ["query"],
            "properties": {
                "query": {"type": "string", "description": "Search query"},
                "count": {"type": "integer", "description": "Max results", "default": 8},
                "recency_days": {"type": "integer", "description": "Prefer recent results", "default": 365},
                "site": {"type": "string", "description": "Optional site filter, e.g. 'docs.ollama.com'"},
                "include_domains": {"type": "array", "items": {"type": "string"}, "description": "Only include results from these domains"},
                "exclude_domains": {"type": "array", "items": {"type": "string"}, "description": "Exclude results from these domains"},
                "safe": {"type": "boolean", "description": "Enable safe search", "default": True}
            }
        }
    ),
    _tool_schema(
        name="web_open",
        description="Fetch a URL and extract clean readable content. Use after web_search.",
        parameters={
            "type": "object",
            "required": ["url"],
            "properties": {
                "url": {"type": "string", "description": "URL to fetch and extract content from"},
                "mode": {"type": "string", "enum": ["auto", "article", "raw"], "description": "Extraction mode", "default": "auto"},
                "max_chars": {"type": "integer", "description": "Maximum characters to extract", "default": 12000},
                "include_html": {"type": "boolean", "description": "Include raw HTML in response", "default": False},
                "follow_links": {"type": "boolean", "description": "Also fetch a few same-site links", "default": False},
                "link_limit": {"type": "integer", "description": "Max same-site links to follow", "default": 3}
            }
        }
    ),
    _tool_schema(
        name="kiwix_search",
        description="Search offline Kiwix ZIM content (full-text) and return ranked hits with internal paths.",
        parameters={
            "type": "object",
            "required": ["query", "zim"],
            "properties": {
                "query": {"type": "string", "description": "Search query"},
                "zim": {"type": "string", "description": "ZIM name/id as served by kiwix-serve"},
                "count": {"type": "integer", "description": "Max results", "default": 8},
                "start": {"type": "integer", "description": "Offset for paging", "default": 0},
            },
        },
    ),
    _tool_schema(
        name="kiwix_open",
        description="Open an offline Kiwix article from a ZIM using an internal path returned by kiwix_search.",
        parameters={
            "type": "object",
            "required": ["zim", "path"],
            "properties": {
                "zim": {"type": "string", "description": "ZIM name/id"},
                "path": {"type": "string", "description": "Internal path inside ZIM, e.g. 'A/Arch_Linux'"},
                "max_chars": {"type": "integer", "description": "Max characters to return", "default": 12000},
            },
        },
    ),
    _tool_schema(
        name="kiwix_suggest",
        description="Suggest offline Kiwix article titles from a ZIM (autocomplete).",
        parameters={
            "type": "object",
            "required": ["zim", "term"],
            "properties": {
                "zim": {"type": "string", "description": "ZIM name/id"},
                "term": {"type": "string", "description": "Search prefix"},
                "count": {"type": "integer", "description": "Max suggestions", "default": 8},
            },
        },
    ),
    _tool_schema(
        name="kiwix_list_zims",
        description="List available ZIM content names by scanning a directory.",
        parameters={
            "type": "object",
            "properties": {
                "zim_dir": {"type": "string", "description": "ZIM directory", "default": "/mnt/zim/zims"},
            },
        },
    ),
]

TOOL_FUNCS = {
    "get_time": tool_get_time,
    "read_file": tool_read_file,
    "web_search": tool_web_search,
    "web_open": tool_web_open,
    "kiwix_search": tool_kiwix_search,
    "kiwix_open": tool_kiwix_open,
    "kiwix_suggest": tool_kiwix_suggest,
    "kiwix_list_zims": tool_kiwix_list_zims,
}


def _coerce_tool_args(args: Any) -> Dict[str, Any]:
    # Ollama returns tool call arguments as an object.
    if args is None:
        return {}
    if isinstance(args, dict):
        return args
    # sometimes models return a JSON string
    if isinstance(args, str):
        try:
            val = json.loads(args)
            if isinstance(val, dict):
                return val
        except json.JSONDecodeError:
            pass
    raise ToolError("tool arguments must be an object")


_CODE_BLOCK = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL | re.IGNORECASE)

def _normalize_tool_obj(parsed: Dict[str, Any], raw: str) -> Optional[Dict[str, Any]]:
    # {"name": "...", "parameters": {...}} or {"name": "...", "arguments": {...}}
    if isinstance(parsed.get("name"), str):
        args = parsed.get("parameters") or parsed.get("arguments")
        if isinstance(args, dict):
            return {"id": f"fallback_{uuid.uuid4().hex}", "function": {"name": parsed["name"], "arguments": args}}

    # {"function": {"name": "...", "arguments": {...}}}
    fn = parsed.get("function")
    if isinstance(fn, dict) and isinstance(fn.get("name"), str):
        args = fn.get("arguments") or fn.get("parameters") or {}
        if isinstance(args, dict):
            return {"id": f"fallback_{uuid.uuid4().hex}", "function": {"name": fn["name"], "arguments": args}}

    return None

def _balanced_json_objects(text: str, max_objects: int = 10) -> List[str]:
    out: List[str] = []
    stack = 0
    start: Optional[int] = None

    for i, ch in enumerate(text):
        if ch == "{":
            if stack == 0:
                start = i
            stack += 1
        elif ch == "}" and stack > 0:
            stack -= 1
            if stack == 0 and start is not None:
                frag = text[start:i+1].strip()
                if len(frag) <= 8000:  # Guard against massive captures
                    out.append(frag)
                if len(out) >= max_objects:
                    break
                start = None
    return out

def _extract_json_candidates(text: str) -> List[str]:
    cands = []
    
    # fenced json blocks first - extract JSON from each code block
    for m in _CODE_BLOCK.finditer(text):
        block_content = m.group(1).strip()
        # Look for JSON objects within the code block
        block_json = _balanced_json_objects(block_content, max_objects=5)
        cands.extend(block_json)

    # balanced JSON objects from the whole content
    cands.extend(_balanced_json_objects(text))
    
    # de-dupe while preserving order
    seen = set()
    out = []
    for x in cands:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out

def _parse_fallback_tool_call(content: str) -> Optional[Dict[str, Any]]:
    """
    Parse tool calls from message content when model doesn't use structured tool_calls.
    Supports formats like:
    - {"name": "web_search", "parameters": {...}}
    - {"function": {"name": "web_search", "arguments": {...}}}
    - Extracts JSON from ```json...``` code blocks
    - Finds embedded JSON in text (LLaMA format)
    """
    if not content or not isinstance(content, str):
        return None

    # Try fenced json blocks first - check all blocks, return first valid
    for m in _CODE_BLOCK.finditer(content):
        block_content = m.group(1).strip()
        # Look for JSON objects within the code block
        for json_match in _balanced_json_objects(block_content, max_objects=5):
            try:
                parsed = json.loads(json_match)
                if isinstance(parsed, dict):
                    call = _normalize_tool_obj(parsed, json_match)
                    if call:
                        call["_source"] = "fallback(codeblock)"
                        return call
            except json.JSONDecodeError:
                continue

    # Try balanced JSON objects in full content
    for cand in _balanced_json_objects(content):
        try:
            parsed = json.loads(cand)
            if isinstance(parsed, dict):
                call = _normalize_tool_obj(parsed, cand)
                if call:
                    call["_source"] = "fallback(balanced)"
                    return call
        except json.JSONDecodeError:
            continue
    return None


def _run_tool_call(call: Dict[str, Any]) -> Dict[str, Any]:
    """
    Turn one Ollama tool_call into a tool message:
      {"role":"tool","tool_name":"...","content":"..."}
    """
    fn = (call or {}).get("function") or {}
    name = fn.get("name")
    raw_args = fn.get("arguments")
    if not name or name not in TOOL_FUNCS:
        raise ToolError(f"unknown tool: {name!r}")
    kwargs = _coerce_tool_args(raw_args)
    try:
        result = TOOL_FUNCS[name](**kwargs)
    except TypeError as e:
        raise ToolError(f"bad arguments for {name}: {e}") from e
    except ToolError:
        raise
    except Exception as e:
        raise ToolError(f"tool {name} failed: {e}") from e
    return {"role": "tool", "tool_name": name, "content": str(result)}


def cmd_list(client: OllamaClient, _args: argparse.Namespace) -> int:
    data = client.tags()
    models = data.get("models", [])
    if not models:
        print("No models found. (Try pulling one: pull <model>)")
        return 0

    for m in models:
        name = m.get("name", "?")
        details = m.get("details", {})
        size = details.get("parameter_size", "")
        quant = details.get("quantization_level", "")
        extra = " ".join(x for x in [size, quant] if x)
        print(f"- {name}" + (f"  [{extra}]" if extra else ""))
    return 0


def cmd_pull(client: OllamaClient, args: argparse.Namespace) -> int:
    for event in client.pull(args.model, stream=True):
        status = event.get("status", "")
        digest = event.get("digest")
        total = event.get("total")
        completed = event.get("completed")

        if total and completed:
            pct = (completed / total) * 100.0
            print(f"{status:>12}  {pct:6.2f}%  {digest or ''}")
        else:
            print(status or event)
    return 0


def cmd_gen(client: OllamaClient, args: argparse.Namespace) -> int:
    options = {}
    if args.temperature is not None:
        options["temperature"] = args.temperature

    out = []
    for event in client.generate(
        model=args.model,
        prompt=args.prompt,
        system=args.system,
        stream=not args.no_stream,
        options=options or None,
        think=args.think,
    ):
        chunk = event.get("response", "")
        if chunk:
            sys.stdout.write(chunk)
            sys.stdout.flush()
            out.append(chunk)

        if event.get("done"):
            break

    if not out:
        print("(no output)")
    else:
        print()
    return 0


def _select_tools(tools_arg: Any) -> Optional[List[Dict[str, Any]]]:
    """Select tools based on argument type."""
    if not tools_arg:
        return None

    # bool True => enable all tools
    if tools_arg is True:
        return TOOL_SPECS

    # list[str] => enable only chosen tools
    if isinstance(tools_arg, list):
        wanted = set(tools_arg)
        return [t for t in TOOL_SPECS if t["function"]["name"] in wanted]

    return TOOL_SPECS  # fallback


def cmd_chat(client: OllamaClient, args: argparse.Namespace) -> int:
    messages: List[Dict[str, Any]] = []
    if args.system:
        # Some people prefer system as first message. Ollama also supports "system" field.
        # We'll use the system field (cleaner).
        system = args.system
    else:
        system = None

    debug_tools = getattr(args, 'debug_tools', False)
    
    # Determine which tools to use
    tools = _select_tools(getattr(args, 'tools', False))

    print("Interactive chat. Type /exit to quit.")
    if tools:
        enabled_tools = [spec["function"]["name"] for spec in tools]
        print(f"Tools enabled: {', '.join(enabled_tools)}")
    while True:
        try:
            user = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0

        if not user:
            continue
        if user.lower() in ("/exit", "/quit"):
            return 0

        messages.append({"role": "user", "content": user})

        # Simple agent loop:
        # - Ask the model
        # - If it asks for tools, run them and feed results back
        # - Repeat until no tool_calls
        while True:
            # Easiest + most reliable for tool_calls: non-stream
            event = next(
                client.chat(
                    model=args.model,
                    messages=messages,
                    system=system,
                    stream=False,
                    think=args.think,
                    tools=tools,
                )
            )
            msg = event.get("message") or {}
            content = (msg.get("content") or "")
            tool_calls = msg.get("tool_calls") or []

            # Fallback parser for models that don't use structured tool_calls
            if not tool_calls and content:
                fallback_call = _parse_fallback_tool_call(content)
                if fallback_call:
                    source = fallback_call.pop("_source", "fallback(unknown)")
                    if debug_tools:
                        print(f"[DEBUG] Parsed fallback tool call: {fallback_call['function']['name']}", file=sys.stderr)
                        print(f"[DEBUG] Tool call source: {source}", file=sys.stderr)
                    tool_calls = [fallback_call]
            elif tool_calls and debug_tools:
                print(f"[DEBUG] Tool call source: structured", file=sys.stderr)

            # Print assistant content (if any)
            if content and not tool_calls:  # Only print non-tool content
                sys.stdout.write(content)
                sys.stdout.flush()
                print()

            # Always append the assistant message so tool_calls are preserved in history
            assistant_entry: Dict[str, Any] = {"role": "assistant"}
            if content:
                assistant_entry["content"] = content
            if tool_calls:
                assistant_entry["tool_calls"] = tool_calls
            messages.append(assistant_entry)

            if not tool_calls:
                break

            # Execute each tool call and append tool results
            for call in tool_calls:
                fn_name = ((call or {}).get("function") or {}).get("name") or "unknown"
                if debug_tools:
                    print(f"[DEBUG] Executing tool: {fn_name}", file=sys.stderr)
                    print(f"[DEBUG] Tool args: {((call or {}).get('function') or {}).get('arguments')}", file=sys.stderr)
                try:
                    tool_msg = _run_tool_call(call)
                    if debug_tools:
                        print(f"[DEBUG] Tool result length: {len(tool_msg.get('content', ''))} chars", file=sys.stderr)
                except ToolError as e:
                    # Feed error back as tool output (so model can recover)
                    tool_msg = {"role": "tool", "tool_name": fn_name, "content": f"ERROR: {e}"}
                    if debug_tools:
                        print(f"[DEBUG] Tool error: {e}", file=sys.stderr)
                messages.append(tool_msg)

    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="ollama-cli", description="Tiny Ollama API CLI")
    p.add_argument("--host", default=DEFAULT_BASE_URL, help="Base host (default: http://localhost:11434)")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub_list = sub.add_parser("list", help="List local models (/api/tags)")
    sub_list.set_defaults(func=cmd_list)

    sub_pull = sub.add_parser("pull", help="Pull a model (/api/pull)")
    sub_pull.add_argument("model", help="Model name (ex: gemma3, llama3.1, qwen2.5)")
    sub_pull.set_defaults(func=cmd_pull)

    sub_gen = sub.add_parser("gen", help="Generate text (/api/generate)")
    sub_gen.add_argument("model", help="Model name")
    sub_gen.add_argument("prompt", help="Prompt string")
    sub_gen.add_argument("--system", help="System prompt")
    sub_gen.add_argument("--temperature", type=float, help="Sampling temperature")
    sub_gen.add_argument("--no-stream", action="store_true", help="Disable streaming")
    sub_gen.add_argument("--think", default=None, help='Enable thinking output (true/false/high/medium/low)')
    sub_gen.set_defaults(func=cmd_gen)

    sub_chat = sub.add_parser("chat", help="Interactive chat (/api/chat)")
    sub_chat.add_argument("model", help="Model name")
    sub_chat.add_argument("--system", help="System prompt")
    sub_chat.add_argument("--think", default=None, help='Enable thinking output (true/false/high/medium/low)')
    sub_chat.add_argument("--tools", action="store_true", help="Enable simple tool calling (get_time, read_file, web_search, web_open)")
    sub_chat.add_argument("--debug-tools", action="store_true", help="Debug tool execution (shows parsed tool calls and results)")
    sub_chat.set_defaults(func=cmd_chat)

    return p


def get_user_choice(prompt: str, options: List[str], default: int = 0) -> int:
    """Get user choice from a list of options."""
    while True:
        print(f"\n{prompt}")
        for i, option in enumerate(options, 1):
            marker = " (default)" if i - 1 == default else ""
            print(f"  {i}. {option}{marker}")
        
        try:
            choice = input(f"Enter choice (1-{len(options)}) [default: {default + 1}]: ").strip()
            if not choice:
                return default
            choice_num = int(choice)
            if 1 <= choice_num <= len(options):
                return choice_num - 1
            else:
                print(f"Please enter a number between 1 and {len(options)}")
        except ValueError:
            print("Please enter a valid number")
        except (EOFError, KeyboardInterrupt):
            print("\nExiting...")
            return -1


def list_available_models(client: OllamaClient) -> List[str]:
    """Get list of available models from Ollama."""
    try:
        data = client.tags()
        models = [m.get("name", "") for m in data.get("models", [])]
        return models if models else []
    except Exception:
        return []


def interactive_startup() -> Dict[str, Any]:
    """Interactive startup prompt for configuration selection."""
    config = {}
    
    print("=== Ollama CLI Configuration ===")
    print("Configure your session settings (press Enter for defaults):")
    
    # Initialize client to get available models
    base_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
    client = OllamaClient(base_url=base_url)
    
    # Model selection
    available_models = list_available_models(client)
    if available_models:
        print(f"\nFound {len(available_models)} available model(s)")
        model_choice = get_user_choice("Select model:", available_models, default=0)
        if model_choice == -1:
            return {}
        config["model"] = available_models[model_choice]
    else:
        print("\nWarning: No models found locally. You'll need to pull a model first.")
        return {}
    
    # Tool selection
    print(f"\nConfigure Tools")
    tool_options = [
        "No tools (chat only)",
        "Basic tools (time, file reading)",
        "Web tools (search, open pages)",
        "All tools (including Kiwix offline content)"
    ]
    tool_choice = get_user_choice("Select tool level:", tool_options, default=1)
    if tool_choice == -1:
        return {}
    
    tool_mapping = {
        0: [],
        1: ["get_time", "read_file"],
        2: ["get_time", "read_file", "web_search", "web_open"],
        3: ["get_time", "read_file", "web_search", "web_open", "kiwix_search", "kiwix_open", "kiwix_suggest", "kiwix_list_zims"]
    }
    config["tools"] = tool_mapping[tool_choice]
    
    # Additional features
    print(f"\nAdditional Features")
    
    # System prompt
    system_prompt = input("Enter system prompt (optional, press Enter to skip): ").strip()
    if system_prompt:
        config["system"] = system_prompt
    
    # Temperature
    temp_input = input("Enter temperature (0.0-2.0, default 0.7, press Enter to skip): ").strip()
    if temp_input:
        try:
            temp = float(temp_input)
            if 0.0 <= temp <= 2.0:
                config["temperature"] = temp
            else:
                print("Temperature must be between 0.0 and 2.0, using default 0.7")
        except ValueError:
            print("Invalid temperature, using default 0.7")
    
    # Thinking mode
    thinking_options = ["Disabled", "Enabled", "High", "Medium", "Low"]
    think_choice = get_user_choice("Select thinking mode:", thinking_options, default=0)
    if think_choice == -1:
        return {}
    
    think_mapping = {0: None, 1: True, 2: "high", 3: "medium", 4: "low"}
    config["think"] = think_mapping[think_choice]
    
    # Save configuration preference
    save_config = input(f"\nSave this configuration for future sessions? (y/N): ").strip().lower()
    if save_config in ['y', 'yes']:
        config["save"] = True
    
    print(f"\nConfiguration complete!")
    print(f"   Model: {config['model']}")
    print(f"   Tools: {len(config['tools'])} tool(s) enabled")
    if config.get("system"):
        print(f"   System: {config['system'][:50]}{'...' if len(config['system']) > 50 else ''}")
    if config.get("temperature") is not None:
        print(f"   Temperature: {config['temperature']}")
    if config.get("think") is not None:
        print(f"   Thinking: {config['think']}")
    
    return config


def save_configuration(config: Dict[str, Any]) -> None:
    """Save configuration to a JSON file."""
    config_file = os.path.expanduser("~/.ollama_cli_config.json")
    try:
        # Remove transient fields before saving
        save_data = {k: v for k, v in config.items() if k != "save"}
        with open(config_file, 'w') as f:
            json.dump(save_data, f, indent=2)
        print(f"Configuration saved to {config_file}")
    except Exception as e:
        print(f"Failed to save configuration: {e}")


def load_configuration() -> Optional[Dict[str, Any]]:
    """Load configuration from JSON file."""
    config_file = os.path.expanduser("~/.ollama_cli_config.json")
    try:
        if os.path.exists(config_file):
            with open(config_file, 'r') as f:
                return json.load(f)
    except Exception:
        pass
    return None


def interactive_or_saved_config() -> Optional[Dict[str, Any]]:
    """Load saved config or run interactive setup."""
    saved_config = load_configuration()
    
    if saved_config:
        use_saved = input(f"Use saved configuration? Model: {saved_config.get('model', 'unknown')} (Y/n): ").strip().lower()
        if use_saved in ['', 'y', 'yes']:
            return saved_config
    
    return interactive_startup()


def build_interactive_parser() -> argparse.ArgumentParser:
    """Build parser for interactive mode."""
    p = argparse.ArgumentParser(prog="ollama-cli", description="Tiny Ollama API CLI with interactive setup")
    p.add_argument("--host", default=DEFAULT_BASE_URL, help="Base host (default: http://localhost:11434)")
    p.add_argument("--no-interactive", action="store_true", help="Skip interactive setup")
    p.add_argument("--reset-config", action="store_true", help="Reset saved configuration")
    sub = p.add_subparsers(dest="cmd", required=True)

    # Keep existing commands for non-interactive mode
    sub_list = sub.add_parser("list", help="List local models (/api/tags)")
    sub_list.set_defaults(func=cmd_list)

    sub_pull = sub.add_parser("pull", help="Pull a model (/api/pull)")
    sub_pull.add_argument("model", help="Model name (ex: gemma3, llama3.1, qwen2.5)")
    sub_pull.set_defaults(func=cmd_pull)

    sub_gen = sub.add_parser("gen", help="Generate text (/api/generate)")
    sub_gen.add_argument("model", help="Model name")
    sub_gen.add_argument("prompt", help="Prompt string")
    sub_gen.add_argument("--system", help="System prompt")
    sub_gen.add_argument("--temperature", type=float, help="Sampling temperature")
    sub_gen.add_argument("--no-stream", action="store_true", help="Disable streaming")
    sub_gen.add_argument("--think", default=None, help='Enable thinking output (true/false/high/medium/low)')
    sub_gen.set_defaults(func=cmd_gen)

    sub_chat = sub.add_parser("chat", help="Interactive chat (/api/chat)")
    sub_chat.add_argument("model", nargs='?', help="Model name (optional if configured)")
    sub_chat.add_argument("--system", help="System prompt")
    sub_chat.add_argument("--think", default=None, help='Enable thinking output (true/false/high/medium/low)')
    sub_chat.add_argument("--tools", action="store_true", help="Enable simple tool calling (get_time, read_file, web_search, web_open)")
    sub_chat.add_argument("--debug-tools", action="store_true", help="Debug tool execution (shows parsed tool calls and results)")
    sub_chat.set_defaults(func=cmd_chat)

    # Add new interactive command
    sub_interactive = sub.add_parser("interactive", help="Start interactive mode with configuration prompts")
    sub_interactive.set_defaults(func=cmd_interactive)

    return p


def cmd_interactive(client: OllamaClient, _args: argparse.Namespace) -> int:
    """Command for interactive mode."""
    config = interactive_or_saved_config()
    if not config:
        return 1
    
    if config.get("save"):
        save_configuration(config)
    
    # Start chat with selected configuration
    return start_configured_chat(client, config)


def start_configured_chat(client: OllamaClient, config: Dict[str, Any]) -> int:
    """Start chat with pre-configured settings."""
    import argparse
    
    model = config["model"]
    system = config.get("system")
    think = config.get("think")
    selected_tools = config.get("tools", [])
    
    # Build arguments for chat command
    args = argparse.Namespace()
    args.model = model
    args.system = system
    args.think = think
    args.tools = selected_tools if selected_tools else False  # Pass the actual list, not just bool
    args.debug_tools = False
    
    return cmd_chat(client, args)


def main() -> int:
    # Check for reset config flag first
    if "--reset-config" in sys.argv:
        config_file = os.path.expanduser("~/.ollama_cli_config.json")
        try:
            os.remove(config_file)
            print("Configuration reset successfully")
        except Exception as e:
            print(f"Failed to reset configuration: {e}")
        return 0
    
    # Check if we should run interactive mode by default (no command provided)
    if len(sys.argv) == 1 or (len(sys.argv) > 1 and sys.argv[1] not in ["list", "pull", "gen", "chat", "--help", "-h"]):
        # Default to interactive mode
        base_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
        client = OllamaClient(base_url=base_url)
        config = interactive_or_saved_config()
        if not config:
            return 1
        
        if config.get("save"):
            save_configuration(config)
        
        return start_configured_chat(client, config)
    
    # Use regular argument parsing for specific commands
    parser = build_interactive_parser()
    args = parser.parse_args()

    # Prefer environment variable for base URL, fallback to args.host
    base_url = os.getenv("OLLAMA_BASE_URL", args.host)
    client = OllamaClient(base_url=base_url)

    try:
        return args.func(client, args)
    except OllamaAPIError as e:
        print(f"API error: {e}", file=sys.stderr)
        print("Is ollama running? Try: sudo systemctl status ollama", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
