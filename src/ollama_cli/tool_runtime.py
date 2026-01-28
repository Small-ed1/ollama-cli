"""Advanced tool runtime system with safety limits and progress tracking.

This module provides the ToolRuntime class that handles async tool execution
with proper safety limits, progress reporting, and error handling.
"""

import asyncio
import inspect
import json
import logging
import os
import time
import uuid
from dataclasses import dataclass
from typing import Any, AsyncGenerator, Dict, Generator, Optional, Union

from .tools.core import get_tool_functions

logger = logging.getLogger(__name__)

# Standardized error codes
class ToolErrorCodes:
    NOT_FOUND = "not_found"
    INVALID_ARGS = "invalid_args"
    TIMEOUT = "timeout"
    EXCEPTION = "exception"
    OUTPUT_TOO_LARGE = "output_too_large"
    NO_RESULT = "no_result"


@dataclass
class ToolResult:
    """Result of a tool execution with metadata."""
    ok: bool
    result: Optional[str] = None
    code: Optional[str] = None
    error: Optional[str] = None
    meta: Optional[Dict[str, Any]] = None
    duration_ms: Optional[float] = None
    result_bytes: Optional[int] = None


class ToolRuntime:
    """Advanced tool runtime with safety limits and progress support.
    
    This runtime provides a unified interface for executing tools with
    proper timeout handling, output limits, and progress reporting.
    """
    
    def __init__(
        self,
        timeout_s: Optional[float] = 60.0,
        max_chunks: int = 1000,
        max_result_bytes: int = 100_000,
    ):
        """Initialize the tool runtime with safety limits.
        
        Args:
            timeout_s: Maximum time per tool call in seconds
            max_chunks: Maximum number of progress chunks to allow
            max_result_bytes: Maximum size of JSON result per call
        """
        self.timeout_s = timeout_s
        self.max_chunks = max_chunks
        self.max_result_bytes = max_result_bytes
        self._tool_funcs = get_tool_functions()
    
    async def call_async(
        self,
        tool_name: str,
        tool_args: Dict[str, Any],
        tool_call_id: Optional[str] = None,
        tool_context: Optional[Dict[str, Any]] = None,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """Execute a tool call with safety limits and progress reporting.
        
        Args:
            tool_name: Name of the tool to execute
            tool_args: Arguments to pass to the tool
            tool_call_id: Optional call ID for tracking
            tool_context: Optional context for security and debugging settings
            
        Yields:
            Progress events and final result
            
        Raises:
            ValueError: If tool name is invalid or arguments are bad
            RuntimeError: If tool execution fails
            asyncio.TimeoutError: If tool execution times out
        """
        start_time = time.time()
        if tool_call_id is None:
            tool_call_id = str(uuid.uuid4())
        
        # Validate tool exists
        if tool_name not in self._tool_funcs:
            duration_ms = (time.time() - start_time) * 1000
            logger.warning(f"Tool not found: {tool_name} (duration: {duration_ms:.1f}ms)")
            logger.info(f"Tool audit: {tool_name} ok=False")
            yield {
                "type": "tool",
                "event": "error",
                "tool": tool_name,
                "tool_call_id": tool_call_id,
                "error": f"Unknown tool: {tool_name}",
                "ok": False,
                "code": ToolErrorCodes.NOT_FOUND,
                "duration_ms": duration_ms,
            }
            return
        
        # Emit tool start event
        yield {
            "type": "tool",
            "event": "start",
            "tool": tool_name,
            "tool_call_id": tool_call_id,
        }
        
        # Inject tool_context if supported
        if tool_context and hasattr(self._tool_funcs[tool_name], '__code__'):
            sig = inspect.signature(self._tool_funcs[tool_name])
            if 'tool_context' in sig.parameters:
                tool_args = tool_args.copy()
                tool_args['tool_context'] = tool_context
            elif 'file_security' in sig.parameters:
                tool_args = tool_args.copy()
                tool_args['file_security'] = tool_context
        
        # Execute tool with timeout
        final_result = None
        final_error = None
        final_code = None
        
        try:
            # For synchronous tools, run them in thread pool
            if inspect.iscoroutinefunction(self._tool_funcs[tool_name]):
                result = await asyncio.wait_for(
                    self._tool_funcs[tool_name](**tool_args),
                    timeout=self.timeout_s,
                )
            else:
                import functools
                result = await asyncio.wait_for(
                    asyncio.get_event_loop().run_in_executor(
                        None, functools.partial(self._tool_funcs[tool_name], **tool_args)
                    ),
                    timeout=self.timeout_s,
                )
            
            # Ensure result is string
            if not isinstance(result, str):
                result = json.dumps(result, indent=2, ensure_ascii=False)
            
            # Check result size
            result_size = len(result.encode('utf-8'))
            if result_size > self.max_result_bytes:
                duration_ms = (time.time() - start_time) * 1000
                logger.warning(f"Tool output too large: {tool_name} ({result_size} bytes, duration: {duration_ms:.1f}ms)")
                final_error = f"Result too large: {result_size} bytes"
                final_code = ToolErrorCodes.OUTPUT_TOO_LARGE
            else:
                final_result = result
                duration_ms = (time.time() - start_time) * 1000
                logger.info(f"Tool success: {tool_name} ({result_size} bytes, duration: {duration_ms:.1f}ms)")
            
        except asyncio.TimeoutError:
            duration_ms = (time.time() - start_time) * 1000
            logger.warning(f"Tool timeout: {tool_name} (duration: {duration_ms:.1f}ms)")
            final_error = f"Tool execution timed out after {self.timeout_s}s"
            final_code = ToolErrorCodes.TIMEOUT
        except Exception as e:
            duration_ms = (time.time() - start_time) * 1000
            logger.error(f"Tool exception: {tool_name} - {e} (duration: {duration_ms:.1f}ms)")
            final_error = str(e)
            final_code = ToolErrorCodes.EXCEPTION
        
        # Log audit info (debug mode includes args)
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(f"Tool audit: {tool_name} ok={final_result is not None} args={tool_args}")
        else:
            logger.info(f"Tool audit: {tool_name} ok={final_result is not None}")
        
        # Emit result or error
        duration_ms = (time.time() - start_time) * 1000
        if final_result is not None:
            yield {
                "type": "tool",
                "event": "result",
                "tool": tool_name,
                "tool_call_id": tool_call_id,
                "result": final_result,
                "ok": True,
                "duration_ms": duration_ms,
                "result_bytes": len(final_result.encode('utf-8')),
            }
        else:
            yield {
                "type": "tool",
                "event": "error",
                "tool": tool_name,
                "tool_call_id": tool_call_id,
                "error": final_error,
                "ok": False,
                "code": final_code,
                "duration_ms": duration_ms,
            }
    
    def call_sync(self, tool_name: str, tool_args: Dict[str, Any], tool_context: Optional[Dict[str, Any]] = None) -> ToolResult:
        """Synchronous version of call_async for backward compatibility.
        
        Args:
            tool_name: Name of the tool to execute
            tool_args: Arguments to pass to the tool
            tool_context: Optional context for security and debugging settings
            
        Returns:
            ToolResult with execution outcome
        """
        start_time = time.time()
        try:
            if tool_name not in self._tool_funcs:
                duration_ms = (time.time() - start_time) * 1000
                logger.warning(f"Tool not found: {tool_name} (duration: {duration_ms:.1f}ms)")
                return ToolResult(
                    ok=False,
                    code=ToolErrorCodes.NOT_FOUND,
                    error=f"Unknown tool: {tool_name}",
                    duration_ms=duration_ms,
                )
            
            # Inject tool_context if supported
            if tool_context and hasattr(self._tool_funcs[tool_name], '__code__'):
                sig = inspect.signature(self._tool_funcs[tool_name])
                if 'tool_context' in sig.parameters:
                    tool_args = tool_args.copy()
                    tool_args['tool_context'] = tool_context
                elif 'file_security' in sig.parameters:
                    tool_args = tool_args.copy()
                    tool_args['file_security'] = tool_context
            
            # Execute tool directly
            result = self._tool_funcs[tool_name](**tool_args)
            
            # Ensure result is string
            if not isinstance(result, str):
                result = json.dumps(result, indent=2, ensure_ascii=False)
            
            # Check result size
            result_size = len(result.encode('utf-8'))
            if result_size > self.max_result_bytes:
                duration_ms = (time.time() - start_time) * 1000
                logger.warning(f"Tool output too large: {tool_name} ({result_size} bytes, duration: {duration_ms:.1f}ms)")
                return ToolResult(
                    ok=False,
                    code=ToolErrorCodes.OUTPUT_TOO_LARGE,
                    error=f"Result too large: {result_size} bytes",
                    duration_ms=duration_ms,
                )
            
            duration_ms = (time.time() - start_time) * 1000
            logger.info(f"Tool success: {tool_name} ({result_size} bytes, duration: {duration_ms:.1f}ms)")
            
            # Log audit info
            if logger.isEnabledFor(logging.DEBUG):
                logger.debug(f"Tool audit: {tool_name} ok=True args={tool_args}")
            else:
                logger.info(f"Tool audit: {tool_name} ok=True")
            
            return ToolResult(
                ok=True,
                result=result,
                meta={"size_bytes": result_size},
                duration_ms=duration_ms,
                result_bytes=result_size,
            )
            
        except Exception as e:
            duration_ms = (time.time() - start_time) * 1000
            logger.error(f"Tool exception: {tool_name} - {e} (duration: {duration_ms:.1f}ms)")
            return ToolResult(
                ok=False,
                code=ToolErrorCodes.EXCEPTION,
                error=str(e),
                duration_ms=duration_ms,
            )


# Runtime cache keyed by dependency signature
_runtime_cache: Dict[str, ToolRuntime] = {}


def _get_runtime_signature() -> str:
    """Create a signature for current runtime configuration."""
    return f"{os.getenv('TOOL_TIMEOUT_S', '60.0')}:{os.getenv('TOOL_MAX_CHUNKS', '1000')}:{os.getenv('TOOL_MAX_RESULT_BYTES', '100000')}"


def get_default_runtime() -> ToolRuntime:
    """Get the default runtime instance with conservative defaults.
    
    Uses caching based on configuration to avoid recreating runtimes
    with the same safety limits, but allows recreation when config changes.
    """
    signature = _get_runtime_signature()
    
    if signature not in _runtime_cache:
        _runtime_cache[signature] = ToolRuntime(
            timeout_s=float(os.getenv("TOOL_TIMEOUT_S", "60.0")),
            max_chunks=int(os.getenv("TOOL_MAX_CHUNKS", "1000")),
            max_result_bytes=int(os.getenv("TOOL_MAX_RESULT_BYTES", "100000")),
        )
    
    return _runtime_cache[signature]


def clear_runtime_cache() -> None:
    """Clear the runtime cache - useful for testing or config changes."""
    global _runtime_cache
    _runtime_cache.clear()


def _tool_engine() -> str:
    """Get the current tool engine setting at runtime."""
    return os.getenv("TOOL_ENGINE", "legacy")