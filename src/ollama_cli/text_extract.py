"""Text extraction utilities - zero tool knowledge."""

import logging
from typing import Any

logger = logging.getLogger(__name__)

# Optional dependency globals
_trafilatura: Any = None
_beautifulsoup: Any = None
_web_deps_installed = False


def ensure_web_deps() -> bool:
    """Validate optional web dependencies without side effects."""
    global _web_deps_installed, _trafilatura, _beautifulsoup
    if _web_deps_installed:
        return True
    
    # Check if dependencies are available by importing actual modules
    missing = []
    # Local optional dependency modules (set to None if unavailable)
    # Optional dependency modules will be bound by the import statements below
    try:
        import trafilatura as _trafilatura_mod  # type: ignore[import-not-found]
    except ImportError:
        _trafilatura_mod = None  # type: ignore
        missing.append('trafilatura')
    try:
        import bs4 as _bs4_mod  # type: ignore[import-not-found]
        
    except ImportError:
        _bs4_mod = None  # type: ignore
        missing.append('beautifulsoup4')  # Package name, not import name

    if missing:
        logger.info(
            "Optional web deps missing: %s. Install with `pip install ollama-cli[web]`.",
            ", ".join(missing),
        )
        return False
    # Bind the loaded modules to the module globals for runtime use
    global _trafilatura, _beautifulsoup
    _trafilatura = _trafilatura_mod
    _beautifulsoup = _bs4_mod
    _web_deps_installed = True
    return True


def html_to_text(html: str) -> str:
    """Convert HTML to readable text.
    
    Extracts the main text content from HTML using trafilatura if available,
    falling back to BeautifulSoup with basic cleaning if needed.
    
    Args:
        html: HTML content to convert
        
    Returns:
        Readable text content
    """
    if not ensure_web_deps():
        return html
    
    # Try trafilatura first (better at extracting main content)
    try:
        if _trafilatura:
            text = _trafilatura.extract(html, favor_precision=True)
            if text:
                return clean_ws(text)
    except Exception:
        pass
    
    # Fallback to BeautifulSoup
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
        return clean_ws(soup.get_text(" "))
    except Exception:
        return clean_ws(html)


def clean_ws(s: str) -> str:
    """Clean whitespace in text.
    
    Normalizes whitespace by collapsing multiple spaces and line breaks
    into single spaces and trimming leading/trailing whitespace.
    
    Args:
        s: Text string to clean
        
    Returns:
        Text with normalized whitespace
    """
    return " ".join(s.split())
