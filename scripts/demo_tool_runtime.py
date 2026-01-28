#!/usr/bin/env python3
"""Demonstration of the new ToolRuntime system.

This script shows both legacy and registry engine usage with progress events.
"""

import asyncio
import os
from tool_parse import run_tool_calling_loop_sync, run_tool_calling_loop
from tool_runtime import get_default_runtime


async def demo_registry_engine():
    """Demonstrate the new registry engine with progress events."""
    print("\n=== Registry Engine Demo ===")
    
    # Set engine to registry
    os.environ["TOOL_ENGINE"] = "registry"
    
    tool_calls = [
        {
            "id": "demo_1",
            "function": {
                "name": "get_time",
                "arguments": {"tz": "UTC"}
            }
        }
    ]
    
    progress_events = []
    
    async def emit(event):
        progress_events.append(event)
        event_type = event.get("event", "unknown")
        if event_type == "start":
            print(f"🔧 Starting tool: {event.get('tool')}")
        elif event_type == "result":
            print(f"✅ Tool completed: {event.get('tool')}")
        elif event_type == "error":
            print(f"❌ Tool error: {event.get('error')}")
    
    results = await run_tool_calling_loop(tool_calls, emit)
    
    print(f"\nProgress events: {len(progress_events)}")
    print(f"Results: {len(results)}")
    if results:
        print(f"Result content: {results[0]['content'][:100]}...")


def demo_legacy_engine():
    """Demonstrate the legacy engine."""
    print("\n=== Legacy Engine Demo ===")
    
    # Set engine to legacy
    os.environ["TOOL_ENGINE"] = "legacy"
    
    tool_calls = [
        {
            "id": "demo_2",
            "function": {
                "name": "get_time",
                "arguments": {"tz": "local"}
            }
        }
    ]
    
    results = run_tool_calling_loop_sync(tool_calls)
    
    print(f"Results: {len(results)}")
    if results:
        print(f"Result content: {results[0]['content'][:100]}...")


def demo_runtime_safety():
    """Demonstrate ToolRuntime safety limits."""
    print("\n=== Safety Limits Demo ===")
    
    from tool_runtime import ToolRuntime
    runtime = ToolRuntime(
        timeout_s=5.0,
        max_chunks=100,
        max_result_bytes=50000
    )
    
    # Test successful call
    result = runtime.call_sync("get_time", {"tz": "UTC"})
    print(f"✅ Successful call: {result.ok}")
    if result.ok:
        print(f"   Size: {result.meta.get('size_bytes', 'unknown')} bytes")
    
    # Test error case
    result = runtime.call_sync("nonexistent_tool", {})
    print(f"❌ Error call: {result.ok}")
    if not result.ok:
        print(f"   Error: {result.error}")


def main():
    """Run all demonstrations."""
    print("ToolRuntime System Demo")
    print("=" * 40)
    
    # Run async demo first
    asyncio.run(demo_registry_engine())
    
    # Run sync demo separately
    demo_legacy_engine()
    demo_runtime_safety()
    
    print("\n" + "=" * 40)
    print("Demo complete! 🎉")


if __name__ == "__main__":
    main()