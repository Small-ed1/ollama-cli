"""Tools package for ollama-cli.

This package provides modular tool implementations with lazy loading
to avoid eager imports of optional dependencies.
"""

from .core import (
    ToolError, WebToolError, KiwixToolError,
    SearchResult, TOOL_SPECS, get_tool_functions, _tool_schema
)

__all__ = [
    'ToolError', 'WebToolError', 'KiwixToolError',
    'SearchResult', 'TOOL_SPECS', 'get_tool_functions', '_tool_schema'
]