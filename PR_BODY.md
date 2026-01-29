## Summary
- Guard optional web dependencies (trafilatura, bs4) for mypy and runtime safety.
- Silence typing warnings for requests in environments without type stubs.
- Ensure tests pass locally after environment upgrade to Python 3.14+.

## Changes
- text_extract.py: robust optional dep handling and binding to globals only when available.
- client.py, web_tools.py, kiwix_tools.py: added type: ignore for requests imports to satisfy mypy in non-stub environments.
- scripts/sync_with_remote.sh: added guard script for syncing with remote.

## Testing
- Local tests: 16 passed (pytest)
- Type checks: clean on Python 3.14+ with mypy

## How to test
- Run tests with PYTHONPATH=src
- Run mypy on src/ollama_cli/

@codex Please review.
