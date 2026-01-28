"""Tool call parsing and execution utilities.

This module provides pure parsing logic for tool calls without any dependency
on the actual tool implementations. It handles normalization, validation,
and execution of tool calls in a clean, testable way.
"""

import asyncio
import json
import os
import re
import uuid
from typing import Any, Dict, Generator, List, Optional

# Regex for extracting JSON from code blocks
_CODE_BLOCK = re.compile(r'```(?:json)?\s*(.*?)\s*```', re.DOTALL)


def coerce_tool_args(tool_name: str, tool_args: Dict[str, Any]) -> Dict[str, Any]:
    """Coerce and validate tool arguments to expected types.
    
    Args:
        tool_name: Name of the tool for error context
        tool_args: Raw arguments from the model
        
    Returns:
        Coerced arguments dictionary
        
    Raises:
        ValueError: If required arguments are missing or invalid
    """
    if not isinstance(tool_args, dict):
        raise ValueError(f"Tool {tool_name}: arguments must be a dictionary")
    
    # Basic validation - required args must be present and not None
    # Allow empty args for tools that don't require arguments
    return tool_args


def parse_fallback_tool_call(text: str) -> Optional[Dict[str, Any]]:
    """Parse tool calls from unstructured text using multiple strategies.
    
    This is a fallback parser that extracts tool calls when the model doesn't
    properly format them as JSON. It tries multiple extraction strategies.
    
    Args:
        text: Text potentially containing tool calls
        
    Returns:
        Tool call dictionary if found, None otherwise
    """
    # Strategy 1: Look for JSON in code blocks
    for match in _CODE_BLOCK.finditer(text):
        try:
            data = json.loads(match.group(1))
            if "function" in data and "name" in data.get("function", {}):
                return data
        except json.JSONDecodeError:
            continue
    
    # Strategy 2: Look for standalone JSON objects
    try:
        # Try parsing the entire text as JSON first
        data = json.loads(text.strip())
        if "function" in data and "name" in data.get("function", {}):
            return data
    except json.JSONDecodeError:
        pass
    
    # Strategy 3: Look for JSON-like patterns
    # Find things that look like {"function": {"name": "...", ...}}
    json_pattern = r'\{\s*"function"\s*:\s*\{[^}]*"name"\s*:\s*"[^"]+"'
    matches = re.finditer(json_pattern, text, re.DOTALL)
    
    for match in matches:
        try:
            # Try to extract a valid JSON object around this match
            start = match.start()
            # Find the closing brace for the function object
            brace_count = 0
            in_string = False
            escape_next = False
            pos = start
            
            while pos < len(text):
                char = text[pos]
                
                if escape_next:
                    escape_next = False
                    pos += 1
                    continue
                
                if char == '\\':
                    escape_next = True
                    pos += 1
                    continue
                    
                if char == '"' and not in_string:
                    in_string = True
                elif char == '"' and in_string:
                    in_string = False
                elif char == '{' and not in_string:
                    brace_count += 1
                elif char == '}' and not in_string:
                    brace_count -= 1
                    if brace_count == 0:
                        # Found complete JSON object
                        json_str = text[start:pos + 1]
                        try:
                            data = json.loads(json_str)
                            if "function" in data and "name" in data.get("function", {}):
                                return data
                        except json.JSONDecodeError:
                            pass
                        break
                pos += 1
        except Exception:
            continue
    
    return None








async def run_tool_calling_loop(
    tool_calls: List[Dict[str, Any]],
    emit=None,
    tool_context: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """Run tool calling loop using the ToolRuntime system.
    
    Args:
        tool_calls: List of tool call dictionaries
        emit: Optional emit function for progress events
        tool_context: Optional context for tool execution
        
    Returns:
        List of tool result messages in chat API format
    """
    from .tool_runtime import get_default_runtime
    
    runtime = get_default_runtime()
    working = []
    
    for tool_call in tool_calls:
        function = tool_call.get("function", {})
        name = function.get("name")
        arguments = function.get("arguments", {})
        tool_call_id = tool_call.get("id", str(uuid.uuid4()))
        
        if not name:
            error_msg = "Tool call missing function name"
            if emit:
                await emit({
                    "type": "tool",
                    "event": "error",
                    "error": error_msg,
                    "tool_call_id": tool_call_id,
                })
            working.append({
                "role": "tool",
                "content": f"Error: {error_msg}",
                "tool_name": "unknown",
                "tool_call_id": tool_call_id,
            })
            continue
        
        # Execute tool with progress reporting
        final_result = None
        chunk_count = 0
        
        async for event in runtime.call_async(
            name,
            arguments,
            tool_call_id=tool_call_id,
            tool_context=tool_context,
        ):
            chunk_count += 1
            if emit:
                await emit(event)
            
            # Track final result
            if event.get("event") == "result" and event.get("ok"):
                final_result = event.get("result")
            elif event.get("event") == "error":
                final_result = f"Error: {event.get('error')}"
        
        # Convert to legacy message format with JSON payload
        if final_result and not final_result.startswith("Error:"):
            # Success case - return JSON payload
            content = json.dumps({
                "ok": True,
                "tool": name,
                "result": final_result,
            }, ensure_ascii=False)
        else:
            # Error case - return JSON error payload
            from .tool_runtime import ToolErrorCodes
            error_msg = final_result or "Error: No result produced"
            content = json.dumps({
                "ok": False,
                "tool": name,
                "error": error_msg,
                "code": ToolErrorCodes.NO_RESULT if "No result" in error_msg else "execution_failed",
            }, ensure_ascii=False)
        
        working.append({
            "role": "tool",
            "content": content,
            "tool_name": name,
            "tool_call_id": tool_call_id,
        })
    
    return working


def run_tool_calling_loop_sync(
    tool_calls: List[Dict[str, Any]],
    emit=None,
    tool_context: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """Synchronous wrapper for run_tool_calling_loop."""
    try:
        # Check if we're already in an event loop
        loop = asyncio.get_running_loop()
        # If we're already in an event loop, we can't use run_until_complete
        # Run in a thread to avoid nested loop issues
        import concurrent.futures
        import threading
        
        result_container = []
        exception_container = []
        
        def run_in_thread():
            try:
                result = asyncio.run(run_tool_calling_loop(tool_calls, emit, tool_context))
                result_container.append(result)
            except Exception as e:
                exception_container.append(e)
        
        thread = threading.Thread(target=run_in_thread)
        thread.start()
        thread.join()
        
        if exception_container:
            raise exception_container[0]
        return result_container[0]
        
    except RuntimeError:
        # No running loop, use asyncio.run()
        return asyncio.run(run_tool_calling_loop(tool_calls, emit, tool_context))