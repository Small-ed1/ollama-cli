"""Text extraction utilities - zero tool knowledge.

This module provides pure utilities for HTML processing and web dependency
management without any tool-specific coupling.
"""

import subprocess
import sys
from typing import Any, Optional

# Optional dependency globals
_trafilatura: Any = None
_beautifulsoup: Any = None
_web_deps_installed = False


def ensure_web_deps() -> bool:
    """Install and validate optional web dependencies.
    
    Installs trafilatura, beautifulsoup4, and readability-lxml on first use
    with graceful fallback if installation fails.
    
    Returns:
        True if dependencies are available, False otherwise
    """
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