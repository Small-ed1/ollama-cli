"""
Ollama CLI - Command-line interface for Ollama with tool capabilities.

A modular Python CLI application that provides command-line access to Ollama API
with additional tool capabilities including web search via SearxNG and offline
content access via Kiwix.
"""

__version__ = "1.0.0"
__author__ = "Ollama CLI Contributors"


def __getattr__(name: str):
    # Lazy exports to avoid importing CLI modules at package import time.
    if name == "main":
        from .cli import main as _main
        return _main
    if name == "OllamaClient":
        from .client import OllamaClient as _OllamaClient
        return _OllamaClient
    raise AttributeError(name)


__all__ = ["main", "OllamaClient"]
