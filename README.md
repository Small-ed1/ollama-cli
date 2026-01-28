# ollama-cli

Command-line interface for the Ollama API with optional tool calling.

Key features:
- Core Ollama workflows: list models, pull models, generate text, chat
- Tool calling for chat: `get_time`, `read_file`, `web_search`, `web_open`, and Kiwix tools
- Deep research mode (search + open + synthesis with citations)

## Requirements

- Python 3.14+
- An Ollama server (default: `http://localhost:11434`)

## Install

```bash
pip install ollama-cli

# Optional extras
pip install "ollama-cli[web]"   # trafilatura + beautifulsoup4 + readability-lxml
pip install "ollama-cli[kiwix]"
pip install "ollama-cli[dev]"
```

From a local checkout:

```bash
pip install .
pip install -e ".[dev]"
```

## Quick start

```bash
ollama-cli --help

ollama-cli list
ollama-cli pull llama3.2
ollama-cli gen llama3.2 "Hello world" --stream
```

Chat:

```bash
ollama-cli chat llama3.2
```

Chat with tools enabled:

```bash
ollama-cli chat llama3.2 --tools --allowed-read-path .
```

Deep research (uses SearxNG unless you provide `--url` seed sources):

```bash
ollama-cli research "How does speculative decoding work?" --preset quick

ollama-cli research "OAuth 2.1 changes from 2.0" \
  --url https://oauth.net/2.1/ \
  --url https://www.rfc-editor.org/rfc/rfc6749
```

## Configuration

Environment variables:
- `OLLAMA_BASE_URL` (default: `http://localhost:11434`)
- `OLLAMA_API_KEY` (optional; sent as `Authorization: Bearer ...`)
- `OLLAMA_MODEL` (used as a default model in some flows)
- `SEARXNG_URL` (default: `http://localhost:8080/search`)
- `KIWIX_URL` (default: `http://127.0.0.1:8080`)

Interactive configuration:
- Running `ollama-cli` with no arguments starts interactive setup on first run.
- Saved config file: `~/.ollama_cli_config.json`
- Reset saved config: `ollama-cli --reset-config`

## Public API (Supported Imports)

- `ollama_cli.client.OllamaClient`
- `ollama_cli.tools.ToolRegistry`
- `ollama_cli.runtime.ToolRuntime`
- `ollama_cli.loop.run_tool_calling_loop` and `ollama_cli.loop.run_tool_calling_loop_sync`
- `ollama_cli.loop.ToolCall` and `ollama_cli.loop.ToolResult` (tool call contract)

All other modules are internal and may change without notice.

## Tool Call Contract

The stable tool contract is modeled as:

- `ToolCall(id, name, arguments)`
- `ToolResult(ok, content, error, meta)`

Tool results are serialized with `ToolResult.to_json()` to guarantee a stable JSON shape across versions.
The tool runtime is async-first; use `run_tool_calling_loop_sync` when you need a sync adapter.

## CogniHub Adapter

Use the optional adapter helpers to integrate with CogniHub:

- `ollama_cli.adapters.cognihub.to_tool_specs(registry) -> list[dict]`
- `ollama_cli.adapters.cognihub.from_ollama_tool_calls(resp) -> list[ToolCall]`

## Tool calling notes

Enable tools:
- `--tools` enables the default set: `get_time`, `read_file`, `web_search`, `web_open`
- `--tool <name>` enables specific tools (repeatable) and overrides `--tools`

File access safety:
- `read_file` is allowlisted by default. Use `--allowed-read-path <path>` (repeatable).
- `--unsafe-read` disables read path restrictions (dangerous).

## Web + offline backends

- SearxNG: required for `web_search` (and used by `research` when you do not pass `--url`). See `docs/SEARXNG_SETUP.md`.
- Kiwix: required for `kiwix_*` tools. Run `kiwix-serve` and set `KIWIX_URL`.

## Development

See `AGENTS.md` for development commands and codebase conventions.
See `CHANGELOG.md` for release notes and `docs/DEPRECATION_POLICY.md` for deprecation guidance.

```bash
pip install -e ".[dev]"

pytest tests/
python -m mypy src/ollama_cli
```

## How it Works

- The CLI is a lightweight shell that talks to an Ollama server via the REST API.
- The client layer (src/ollama_cli/client.py) builds requests to /api endpoints such as tags, pull, generate, and chat.
- The tool system (src/ollama_cli/tools/core.py) registers available tools with JSON schemas. The CLI can enable a subset of tools for chat sessions.
- The interactive and deep‑research features glue together: the CLI collects user input, calls the Ollama model, and uses web/kiwix tools to fetch content and produce a cited report via the model prompts.

Flow example:
- User runs: ollama-cli chat llama3.2 --tools
- CLI loads config, picks a model, and establishes a connection to the Ollama API.
- The chat engine streams messages through the API; when tools are invoked, the tool system calls the corresponding tool function (web_search, read_file, etc.) and returns results to the model.
- For deep research, the pipeline uses web_tools to search, kiwix_tools to fetch offline data, and a prompts-based synthesis stage to generate a cited report.

If you want more, I can add a sequence diagram or example log, but text flow suffices for now.

## Common Errors and Fixes

- Could not connect to Ollama at http://localhost:11434
  - Ensure the Ollama server is running and listening on that URL.
  - If you run Ollama on a different host/port, set OLLAMA_BASE_URL accordingly.
  - Test reachability: curl http://localhost:11434/api/tags

- Authorization header missing or rejected
  - If you provide an API key, export OLLAMA_API_KEY and ensure your server expects it.
  - If your Ollama instance does not require an API key, you can omit this header.

- Timeouts or slow responses
  - Increase client timeout by setting `OLLAMA_TIMEOUT` or passing `ClientConfig(timeout_s=...)`.
  - Check network latency or server load.

- JSON parsing errors from API
  - Confirm the Ollama server is healthy; a non-JSON response may indicate a server error or wrong URL.
  - Check logs on the Ollama side.

- Tool not found / unsupported tool
  - Ensure the tool name exists in TOOL_SPECS (get_time, read_file, web_search, web_open, kiwix_*).
  - If you added a new tool, add a corresponding spec in the tool registry and update imports.

- Read safety blocks
  - If read_file returns an access denied, you likely hit allowed_paths or unsafe_mode checks. Add an allowed path via --allowed-read-path or adjust configuration.

- Kiwix / SearxNG dependencies
  - For web_open/web_search, ensure SEARXNG_URL is reachable and that the settings enable JSON output.
  - For kiwix tools, ensure kiwix-serve is running and KIWIX_URL points to it.

- Mypy/type errors in development
  - Run python -m mypy and fix type hints. This project targets Python 3.14 typing with Optional and Dict generics.

## Best Practices

- Use a dedicated virtual environment for development and tests.
- Run tests before committing: pytest tests/; run mypy ollama_cli/.
- Use descriptive, ticket-friendly commit messages that focus on why.
- Keep docs in README up to date; if you add a new tool or option, reflect it here.
- Prefer new feature work on a branch (e.g., feature/xxx) and open a PR.
- Do not push secrets (API keys, tokens) into the repo.
- When adding new features, write tests that exercise the end-to-end flow (CLI -> API -> tool results).

## How to Contribute

- Fork the repo, create a feature branch, and open a PR.
- Add tests for any new functionality.
- Update README with usage notes and any new options.
- Ensure CI passes (tests and static checks) before merging.
