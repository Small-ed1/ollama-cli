# AGENTS.md - Ollama CLI Development Guidelines

This file contains development guidelines and commands for agentic coding agents working on this ollama-cli Python project.

## Project Overview

This is a modular Python CLI application that provides command-line access to Ollama API with additional tool capabilities including web search via SearxNG and offline content access via Kiwix.

### Architecture
- **Modular structure**: Core functionality split across modules under `src/ollama_cli/`
  - `cli.py`: Main CLI interface and argument parsing (320 lines)
  - `client.py`: Ollama API client (220 lines) 
  - `interactive.py`: Interactive mode functionality
  - `tool_parse.py`: Tool call parsing and execution (392 lines)
  - `tool_runtime.py`: Advanced tool runtime system
  - `config.py`: Configuration management
  - `text_extract.py`: Web content extraction
  - `tools/`: Tool implementations (web_tools, kiwix_tools, system_tools)
- **Object-oriented**: `OllamaClient`, tool classes with inheritance
- **Tool system**: Function-based tool calling with JSON schema definitions
- **CLI**: argparse-based with subcommands (list, pull, gen, chat, interactive)

## Development Commands

### Testing
```bash
# Install in editable mode with dev dependencies first
pip install -e . && pip install pytest pytest-asyncio

# Run all tests
pytest tests/

# Run single test file
pytest tests/test_tool_runtime_integration.py

# Run single test function
pytest tests/test_tool_runtime_integration.py::TestToolRuntimeIntegration::test_legacy_engine_sync

# Run with verbose output
pytest tests/ -v

# Run specific test pattern
pytest tests/ -k "test_legacy_engine"

# Run tests with coverage (if coverage installed)
pytest tests/ --cov=ollama_cli
```

### Code Quality
```bash
# Type checking (run on all installed package modules)
python -m mypy ollama_cli/

# Type checking single file
python -m mypy ollama_cli/cli.py

# Type checking with strict mode
python -m mypy ollama_cli/ --strict

# No auto-formatter configured (no black/ruff)
# Manual formatting required - follow existing style
```

### Running the Application
```bash
# Install in editable mode first (if not already done)
pip install -e .

# Using the installed command
ollama-cli list

# Pull a model
ollama-cli pull gemma3

# Generate text
ollama-cli gen gemma3 "Hello world"

# Interactive chat with tools
ollama-cli chat gemma3 --tools --debug-tools

# Interactive mode with setup prompts
ollama-cli interactive
```

## Code Style Guidelines

### Imports - CRITICAL
- **Use relative imports within the package**: `from .client import OllamaClient`
- **Use absolute imports from package root**: `from src.ollama_cli.client import OllamaClient`
- **Ordering**: Standard library first, then third-party, then local
- **Style**: No unused imports, group related imports together

```python
# Standard library
import argparse
import json
import os
import sys
from dataclasses import dataclass
from typing import Any, Dict, Optional

# Third-party
import requests

# Local modules - USE RELATIVE IMPORTS within package
from .client import OllamaClient
from .tools.core import TOOL_SPECS, get_tool_functions
```

### Type Hints
- **Required**: All function parameters and return types must be typed
- **Style**: Use `Optional[T]` for nullable, `List[T]`, `Dict[K, V]`
- **Python 3.14 compatibility**: Use `Optional[T]` not `T | None`
- **Complex types**: Use `Dict[str, Any]` for flexible JSON-like structures

```python
def web_search(self, query: str, count: int = 8) -> Dict[str, Any]:
    """Search the web and return results."""
    pass

class OllamaClient:
    def __init__(self, base_url: str = DEFAULT_BASE_URL, timeout: int = 60):
        pass
```

### Naming Conventions
- **Classes**: `PascalCase` (e.g., `OllamaClient`, `WebTools`)
- **Functions/variables**: `snake_case` (e.g., `web_search`, `base_url`)
- **Constants**: `UPPER_SNAKE_CASE` (e.g., `DEFAULT_BASE_URL`)
- **Private**: `_underscore_prefix` for internal methods

### Error Handling
- **Custom exceptions**: Inherit from `RuntimeError` or base classes
```python
class OllamaAPIError(RuntimeError):
    pass

class ToolError(RuntimeError):
    pass
```

- **Exception hierarchy**: Use inheritance for specific tool errors
```python
class WebToolError(ToolError):
    """Exception raised when web-based tools fail."""
    pass

class KiwixToolError(ToolError):
    """Exception raised when Kiwix-based tools fail."""
    pass
```

- **Pattern**: Catch specific exceptions, wrap with context, re-raise
```python
try:
    response = requests.post(url, json=payload, timeout=self.timeout)
    response.raise_for_status()
    return response.json()
except requests.RequestException as e:
    raise OllamaAPIError(f"API request failed: {e}") from e
```

### Documentation
- **Docstrings**: Triple quotes, one-line summary + details if needed
- **Style**: Google-like but concise, describe behavior not implementation

```python
def web_open(self, url: str, max_chars: int = 12000) -> Dict[str, Any]:
    """Fetch a URL and extract readable content.
    
    Args:
        url: URL to fetch
        max_chars: Maximum characters to return
        
    Returns:
        Dict with extracted content and metadata
    """
    pass
```

### Code Organization
- **Classes first**: Data structures and main classes at top
- **Functions second**: Utility and command functions
- **Constants**: Global constants after imports
- **Main execution**: `if __name__ == "__main__":` block at bottom

### Logging and Debugging
- **Debug output**: Use `print(..., file=sys.stderr)` for debug messages
- **Debug flags**: Use `debug_tools` boolean flags for optional debug output
- **Error context**: Include relevant parameters in error messages

### API Design Patterns
- **Streaming**: Generators for API responses that support streaming
```python
def _post_stream(self, path: str, payload: Dict[str, Any]) -> Generator[Dict[str, Any], None, None]:
    """Stream JSON responses line by line."""
    pass
```

- **Tool calling**: JSON schema-based tool definitions with function mapping
- **Graceful degradation**: Optional dependencies with fallback behavior

## Environment Setup

### Dependencies
- **Core**: Python 3.14+, `requests`
- **Optional**: `trafilatura`, `beautifulsoup4` (installed on demand)
- **Development**: `pytest`, `mypy`

### Environment Variables
```bash
# Ollama configuration
OLLAMA_BASE_URL="http://localhost:11434"
OLLAMA_API_KEY="your-api-key"  # optional

# External services
SEARXNG_URL="http://localhost:8080/search"
KIWIX_URL="http://127.0.0.1:8080"
```

### Testing External Dependencies
- **SearxNG**: Requires JSON format enabled in settings.yml
- **Kiwix**: Requires kiwix-serve running with ZIM files
- **Optional deps**: Check with `_ensure_web_deps()` pattern

## Tool System Guidelines

### Adding New Tools
1. **Function**: Create `tool_<name>(...)` function with proper error handling
2. **Schema**: Add to `TOOL_SPECS` list with JSON schema
3. **Mapping**: Add to `TOOL_FUNCS` dictionary
4. **Documentation**: Include parameters and return values in docstring

### Tool Function Pattern
```python
def tool_my_tool(param1: str, param2: int = 10) -> str:
    """Tool description for the model."""
    if not param1:
        raise ToolError("param1 is required")
    
    try:
        result = do_work(param1, param2)
        return json.dumps(result, indent=2, ensure_ascii=False)
    except Exception as e:
        raise ToolError(f"my_tool failed: {e}") from e
```

## Common Patterns

### Configuration Management
- Use `os.getenv()` with defaults for configuration
- Support both CLI args and environment variables
- Provide sensible defaults for all settings

### HTTP Client Patterns
- Use `requests.Session()` for connection reuse
- Set appropriate timeouts and headers
- Handle rate limiting with `_rate_limit()` pattern
- Use context managers for requests when possible

### Data Validation
- Validate inputs at function entry
- Use type hints for documentation and mypy checking
- Raise descriptive errors for invalid inputs
- Sanitize external data before processing

## Security & Performance

- **File access**: Restrict to safe directories, validate paths
- **Network access**: Use timeouts, validate URLs, rate limiting
- **Caching**: Implement time-based caching for expensive operations
- **Streaming**: Use generators for large responses

## Testing Guidelines

### Test Structure
- **Integration tests**: Focus on tool runtime and end-to-end functionality
- **Test files**: Place in `tests/` directory with `test_` prefix
- **Test classes**: Use descriptive names like `TestToolRuntimeIntegration`
- **Fixtures**: Use `setup_method` and `teardown_method` for test lifecycle

### Test Patterns
```python
class TestMyFeature:
    def setup_method(self):
        """Set up test environment."""
        pass
    
    def teardown_method(self):
        """Clean up test environment."""
        pass
    
    def test_specific_behavior(self):
        """Test specific behavior with clear assertion."""
        assert result == expected_value
```

### Running Tests
- Always run from project root to ensure proper module resolution
- Use `-v` flag for verbose output during development
- Use `-k` flag to run specific test patterns
- Fix import issues in tests by using proper relative imports