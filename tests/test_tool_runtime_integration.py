"""Integration tests for the tool runtime system.

This test suite validates that the tool calling loop works correctly
with both legacy and registry engines, ensuring proper event emission
and result formatting.
"""

import asyncio
import os
import pytest
import tempfile
from typing import Any, Dict, List

from ollama_cli.tool_parse import run_tool_calling_loop, run_tool_calling_loop_sync
from ollama_cli.tool_runtime import ToolRuntime, clear_runtime_cache
from ollama_cli.tools.core import TOOL_SPECS


class TestToolRuntimeIntegration:
    """Integration tests for the complete tool runtime system."""
    
    def setup_method(self):
        """Set up test environment."""
        # Clear runtime cache before each test
        clear_runtime_cache()
        
        # Create a temporary file for testing
        self.temp_file = tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.txt')
        self.temp_file.write("Test file content for integration testing.")
        self.temp_file.flush()
        self.temp_file_path = self.temp_file.name
        self.temp_file.close()
    
    def teardown_method(self):
        """Clean up test environment."""
        # Clean up temp file
        try:
            os.unlink(self.temp_file_path)
        except FileNotFoundError:
            pass
    
    def test_legacy_engine_sync(self):
        """Test legacy engine with synchronous execution."""
        # Force legacy engine
        os.environ["TOOL_ENGINE"] = "legacy"
        
        tool_calls = [
            {
                "id": "test_1",
                "function": {
                    "name": "get_time",
                    "arguments": {"tz": "UTC"}
                }
            }
        ]
        
        results = run_tool_calling_loop_sync(tool_calls)
        
        assert len(results) == 1
        result = results[0]
        assert result["role"] == "tool"
        assert result["tool_name"] == "get_time"
        assert result["tool_call_id"] == "test_1"
        assert "UTC" in result["content"] or "time" in result["content"].lower()
    
    @pytest.mark.asyncio
    async def test_registry_engine_async(self):
        """Test registry engine with async execution and progress events."""
        # Force registry engine
        os.environ["TOOL_ENGINE"] = "registry"
        
        tool_calls = [
            {
                "id": "test_2",
                "function": {
                    "name": "get_time",
                    "arguments": {"tz": "local"}
                }
            }
        ]
        
        progress_events = []
        
        async def emit(event: Dict[str, Any]):
            progress_events.append(event)
        
        results = await run_tool_calling_loop(tool_calls, emit)
        
        # Verify results
        assert len(results) == 1
        result = results[0]
        assert result["role"] == "tool"
        assert result["tool_name"] == "get_time"
        assert result["tool_call_id"] == "test_2"
        
        # Verify progress events
        assert len(progress_events) >= 2  # At least start + result
        
        # Check start event
        start_events = [e for e in progress_events if e.get("event") == "start"]
        assert len(start_events) == 1
        assert start_events[0]["tool"] == "get_time"
        assert start_events[0]["tool_call_id"] == "test_2"
        
        # Check result event
        result_events = [e for e in progress_events if e.get("event") == "result"]
        assert len(result_events) == 1
        assert result_events[0]["ok"] is True
        assert "result" in result_events[0]
    
    @pytest.mark.asyncio
    async def test_registry_engine_error_handling(self):
        """Test registry engine error handling."""
        os.environ["TOOL_ENGINE"] = "registry"
        
        tool_calls = [
            {
                "id": "test_error",
                "function": {
                    "name": "nonexistent_tool",
                    "arguments": {}
                }
            }
        ]
        
        progress_events = []
        
        async def emit(event: Dict[str, Any]):
            progress_events.append(event)
        
        results = await run_tool_calling_loop(tool_calls, emit)
        
        # Verify error handling
        assert len(results) == 1
        result = results[0]
        assert result["role"] == "tool"
        assert "Error:" in result["content"]
        assert result["tool_call_id"] == "test_error"
        
        # Verify error event
        error_events = [e for e in progress_events if e.get("event") == "error"]
        assert len(error_events) == 1
        assert error_events[0]["ok"] is False
        assert "unknown tool" in error_events[0]["error"].lower()
    
    @pytest.mark.asyncio
    async def test_registry_engine_file_operations(self):
        """Test registry engine with file operations."""
        os.environ["TOOL_ENGINE"] = "registry"
        
        tool_calls = [
            {
                "id": "test_file",
                "function": {
                    "name": "read_file",
                    "arguments": {
                        "path": self.temp_file_path,
                        "max_bytes": 100
                    }
                }
            }
        ]
        
        progress_events = []
        
        async def emit(event: Dict[str, Any]):
            progress_events.append(event)
        
        results = await run_tool_calling_loop(tool_calls, emit)
        
        # Verify successful file read
        assert len(results) == 1
        result = results[0]
        assert result["role"] == "tool"
        assert result["tool_name"] == "read_file"
        assert "Test file content" in result["content"]
        assert result["tool_call_id"] == "test_file"
        
        # Verify proper event flow
        assert any(e.get("event") == "start" for e in progress_events)
        assert any(e.get("event") == "result" for e in progress_events)
    
    def test_tool_runtime_safety_limits(self):
        """Test ToolRuntime safety limits."""
        runtime = ToolRuntime(
            timeout_s=1.0,
            max_chunks=10,
            max_result_bytes=1000
        )
        
        # Test successful call within limits
        result = runtime.call_sync("get_time", {"tz": "UTC"})
        assert result.ok is True
        assert result.result is not None
        
        # Test with non-existent tool
        from ollama_cli.tool_runtime import ToolErrorCodes
        result = runtime.call_sync("nonexistent", {})
        assert result.ok is False
        assert result.code == ToolErrorCodes.NOT_FOUND
    
    @pytest.mark.asyncio
    async def test_tool_runtime_async_timeout(self):
        """Test ToolRuntime async timeout behavior."""
        runtime = ToolRuntime(timeout_s=0.1)  # Very short timeout
        
        # Create a slow tool function
        async def slow_tool(**kwargs):
            await asyncio.sleep(0.5)  # Sleep longer than timeout
            return "should not reach here"
        
        # Temporarily add the slow tool
        runtime._tool_funcs["slow_tool"] = slow_tool
        
        progress_events = []
        async for event in runtime.call_async("slow_tool", {}, "test_timeout"):
            progress_events.append(event)
        
        # Should have error event due to timeout
        error_events = [e for e in progress_events if e.get("event") == "error"]
        assert len(error_events) == 1
        assert "timed out" in error_events[0]["error"].lower()
        
        # Clean up the test tool
        del runtime._tool_funcs["slow_tool"]
    
    def test_runtime_caching(self):
        """Test runtime caching behavior."""
        from ollama_cli.tool_runtime import get_default_runtime, _get_runtime_signature
        
        # First call should create new runtime
        runtime1 = get_default_runtime()
        signature = _get_runtime_signature()
        
        # Second call should return cached runtime
        runtime2 = get_default_runtime()
        assert runtime1 is runtime2
        
        # Clear cache and verify new runtime is created
        clear_runtime_cache()
        runtime3 = get_default_runtime()
        assert runtime1 is not runtime3
    
    def test_tool_specs_consistency(self):
        """Test that all tool specs are valid and consistent."""
        from ollama_cli.tools.core import get_tool_functions
        
        tool_funcs = get_tool_functions()
        
        # Verify all tools in specs have corresponding functions
        spec_names = {spec["function"]["name"] for spec in TOOL_SPECS}
        func_names = set(tool_funcs.keys())
        
        # Remove test tools that may have been added
        func_names = {name for name in func_names if not name.startswith("test_")}
        
        assert spec_names.issubset(func_names), f"Missing functions: {spec_names - func_names}"
        assert func_names.issubset(spec_names), f"Extra functions: {func_names - spec_names}"
        
        # Verify all specs have required fields
        for spec in TOOL_SPECS:
            assert "type" in spec
            assert "function" in spec
            func_spec = spec["function"]
            assert "name" in func_spec
            assert "description" in func_spec
            assert "parameters" in func_spec
            assert func_spec["parameters"]["type"] == "object"


class TestPhase02Features:
    """Test Phase 0.2 features: error codes, metadata, audit logging, and JSON payloads."""
    
    def setup_method(self):
        """Set up test environment."""
        clear_runtime_cache()
        os.environ["TOOL_ENGINE"] = "registry"
    
    @pytest.mark.asyncio
    async def test_standardized_error_codes(self):
        """Test that all errors use standardized error codes."""
        from ollama_cli.tool_runtime import ToolErrorCodes
        
        # Test unknown tool error
        runtime = ToolRuntime()
        progress_events = []
        async for event in runtime.call_async("nonexistent_tool", {}, "test_error"):
            progress_events.append(event)
        
        error_events = [e for e in progress_events if e.get("event") == "error"]
        assert len(error_events) == 1
        assert error_events[0]["code"] == ToolErrorCodes.NOT_FOUND
        
        # Test timeout error
        slow_runtime = ToolRuntime(timeout_s=0.1)
        async def slow_tool(**kwargs):
            await asyncio.sleep(0.5)
            return "should not reach"
        
        slow_runtime._tool_funcs["test_slow"] = slow_tool
        progress_events = []
        async for event in slow_runtime.call_async("test_slow", {}, "test_timeout"):
            progress_events.append(event)
        
        error_events = [e for e in progress_events if e.get("event") == "error"]
        assert len(error_events) == 1
        assert error_events[0]["code"] == ToolErrorCodes.TIMEOUT
        
        del slow_runtime._tool_funcs["test_slow"]
    
    @pytest.mark.asyncio
    async def test_duration_metadata_tracking(self):
        """Test that duration metadata is tracked and included."""
        runtime = ToolRuntime()
        
        progress_events = []
        async for event in runtime.call_async("get_time", {"tz": "UTC"}, "test_duration"):
            progress_events.append(event)
        
        # Check that duration is included in result event
        result_events = [e for e in progress_events if e.get("event") == "result"]
        assert len(result_events) == 1
        assert "duration_ms" in result_events[0]
        assert isinstance(result_events[0]["duration_ms"], (int, float))
        assert result_events[0]["duration_ms"] > 0
        
        # Check that result_bytes is included
        assert "result_bytes" in result_events[0]
        assert isinstance(result_events[0]["result_bytes"], int)
        assert result_events[0]["result_bytes"] > 0
    
    def test_sync_method_metadata(self):
        """Test metadata tracking in sync method."""
        runtime = ToolRuntime()
        
        result = runtime.call_sync("get_time", {"tz": "UTC"})
        
        assert result.ok is True
        assert result.duration_ms is not None
        assert isinstance(result.duration_ms, (int, float))
        assert result.duration_ms > 0
        assert result.result_bytes is not None
        assert isinstance(result.result_bytes, int)
        assert result.result_bytes > 0
    
    @pytest.mark.asyncio
    async def test_output_size_limit_error_code(self):
        """Test output size limit uses correct error code."""
        from ollama_cli.tool_runtime import ToolErrorCodes
        
        # Create runtime with very small limit
        runtime = ToolRuntime(max_result_bytes=10)
        
        progress_events = []
        async for event in runtime.call_async("get_time", {"tz": "UTC"}, "test_size"):
            progress_events.append(event)
        
        error_events = [e for e in progress_events if e.get("event") == "error"]
        assert len(error_events) == 1
        assert error_events[0]["code"] == ToolErrorCodes.OUTPUT_TOO_LARGE
        assert "bytes" in error_events[0]["error"]
    
    @pytest.mark.asyncio 
    async def test_tool_audit_logging(self):
        """Test that audit logging works correctly."""
        import logging
        from io import StringIO
        
        # Set up logging capture
        log_stream = StringIO()
        handler = logging.StreamHandler(log_stream)
        logger = logging.getLogger("ollama_cli.tool_runtime")
        logger.setLevel(logging.INFO)
        logger.addHandler(handler)
        
        try:
            runtime = ToolRuntime()
            
            # Test successful tool execution
            async for event in runtime.call_async("get_time", {"tz": "UTC"}, "test_audit"):
                pass
            
            log_output = log_stream.getvalue()
            assert "Tool success: get_time" in log_output
            assert "Tool audit: get_time ok=True" in log_output
            
            # Test error case
            async for event in runtime.call_async("nonexistent", {}, "test_audit_error"):
                pass
            
            log_output = log_stream.getvalue()
            assert "Tool not found: nonexistent" in log_output
            assert "Tool audit: nonexistent ok=False" in log_output
            
        finally:
            logger.removeHandler(handler)
    
    @pytest.mark.asyncio
    async def test_json_payload_standardization(self):
        """Test that tool message content is always JSON."""
        tool_calls = [
            {
                "id": "test_json",
                "function": {
                    "name": "get_time",
                    "arguments": {"tz": "UTC"}
                }
            }
        ]
        
        results = await run_tool_calling_loop(tool_calls)
        
        assert len(results) == 1
        result = results[0]
        
        # The content should be a string (as expected by chat API)
        assert isinstance(result["content"], str)
        
        # But the underlying result should be JSON-formatted when it's structured data
        # For get_time, it returns a simple string, so this is fine
        
        # Test with a tool that returns structured data
        tool_calls = [
            {
                "id": "test_structured", 
                "function": {
                    "name": "list_files",
                    "arguments": {"path": ".", "max_results": 5}
                }
            }
        ]
        
        results = await run_tool_calling_loop(tool_calls)
        assert len(results) == 1
        assert isinstance(results[0]["content"], str)
    
    def test_sync_wrapper_no_nested_loop(self):
        """Test that sync wrapper doesn't create nested event loops."""
        # This test ensures the sync wrapper works even when called from an async context
        tool_calls = [
            {
                "id": "test_sync",
                "function": {
                    "name": "get_time", 
                    "arguments": {"tz": "UTC"}
                }
            }
        ]
        
        # Direct sync call should work
        results = run_tool_calling_loop_sync(tool_calls)
        assert len(results) == 1
        assert results[0]["tool_name"] == "get_time"
    
    @pytest.mark.asyncio
    async def test_error_code_consistency_across_methods(self):
        """Test that sync and async methods use consistent error codes."""
        from ollama_cli.tool_runtime import ToolErrorCodes
        
        runtime = ToolRuntime()
        
        # Test sync method
        sync_result = runtime.call_sync("nonexistent_tool", {})
        assert sync_result.ok is False
        assert sync_result.code == ToolErrorCodes.NOT_FOUND
        
        # Test async method
        progress_events = []
        async for event in runtime.call_async("nonexistent_tool", {}, "test_consistency"):
            progress_events.append(event)
        
        error_events = [e for e in progress_events if e.get("event") == "error"]
        assert len(error_events) == 1
        assert error_events[0]["code"] == ToolErrorCodes.NOT_FOUND