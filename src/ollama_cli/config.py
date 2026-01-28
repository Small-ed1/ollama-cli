"""Configuration defaults and environment variable handling.

This module centralizes all configuration with environment variable fallbacks
and provides sensible defaults for different environments.
"""

import os
import sys
from typing import Optional


def _debug_print(msg: str) -> None:
    """Print debug message to stderr if in debug mode.
    
    Args:
        msg: Message to print
    """
    if os.getenv("OLLAMA_DEBUG"):
        print(f"[DEBUG] {msg}", file=sys.stderr)


def _env_int(name: str, default: int) -> int:
    """Safely get integer from environment variable.
    
    Args:
        name: Environment variable name
        default: Default value if var doesn't exist or isn't numeric
        
    Returns:
        Integer value from env var or default
    """
    env_val = os.getenv(name, default)
    try:
        return int(env_val)
    except ValueError:
        _debug_print(f"Invalid integer in {name}={env_val!r}, using default {default}")
        return default


def _env_float(name: str, default: float) -> float:
    """Safely get float from environment variable.
    
    Args:
        name: Environment variable name
        default: Default value if var doesn't exist or isn't numeric
        
    Returns:
        Float value from env var or default
    """
    env_val = os.getenv(name, default)
    try:
        return float(env_val)
    except ValueError:
        _debug_print(f"Invalid float in {name}={env_val!r}, using default {default}")
        return default


def _normalize_api_key(api_key: Optional[str]) -> Optional[str]:
    """Normalize API key if provided.
    
    Args:
        api_key: API key to normalize
        
    Returns:
        Normalized API key or None
    """
    if not api_key:
        return None
    
    api_key = api_key.strip()
    return api_key or None


# Ollama API configuration
DEFAULT_BASE_URL: str = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
DEFAULT_OLLAMA_API_KEY: Optional[str] = _normalize_api_key(os.getenv("OLLAMA_API_KEY"))

# External service URLs
DEFAULT_SEARXNG_URL: str = os.getenv("SEARXNG_URL", "http://localhost:8080/search")
DEFAULT_KIWIX_URL: str = os.getenv("KIWIX_URL", "http://127.0.0.1:8080")

# Timeouts and caching
DEFAULT_TIMEOUT: int = _env_int("OLLAMA_TIMEOUT", 60)
DEFAULT_CACHE_MINUTES: int = _env_int("OLLAMA_CACHE_MINUTES", 30)

# Tool-specific defaults
DEFAULT_WEB_SEARCH_COUNT: int = _env_int("OLLAMA_WEB_SEARCH_COUNT", 8)
DEFAULT_WEB_MAX_CHARS: int = _env_int("OLLAMA_WEB_MAX_CHARS", 12000)
DEFAULT_KIWIX_SEARCH_COUNT: int = _env_int("OLLAMA_KIWIX_SEARCH_COUNT", 8)
DEFAULT_KIWIX_MAX_CHARS: int = _env_int("OLLAMA_KIWIX_MAX_CHARS", 12000)

# Configuration file location
DEFAULT_CONFIG_FILE: str = os.path.expanduser("~/.ollama_cli_config.json")