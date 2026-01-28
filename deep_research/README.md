## Deep Research (Ollama + SearxNG + Kiwix)

A balanced deep-research utility that combines multiple sources:

- **SearxNG** for web search (with optional fetch)
- **Kiwix** for offline search/open via configurable HTTP adapter  
- **Ollama** for planner/extractor/writer (tool-calls supported)

Features balanced research length via budgets, conflict resolution, and natural language output with inline citations + sources list.

### Setup

1. **Create virtual environment and install dependencies**
   ```bash
   cd deep_research
   python -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```

2. **Configure**
   ```bash
   cp config.example.json config.json
   # Edit config.json with your service URLs and model names
   ```

3. **Run**
   ```bash
   python -m research.main "your query here" --preset standard
   ```

### Requirements

- **Python 3.14+**
- **SearxNG** instance with `/search?format=json` support
- **Kiwix** server running with ZIM files
- **Ollama** server with compatible models

### Configuration

Edit `config.json`:

```json
{
  "ollama": {
    "base_url": "http://127.0.0.1:11434",
    "models": {
      "planner": "llama3.2:latest",
      "extractor": "llama3.2:latest", 
      "writer": "llama3.2:latest"
    }
  },
  "searxng": {
    "base_url": "http://127.0.0.1:8080"
  },
  "kiwix": {
    "base_url": "http://127.0.0.1:8181",
    "zim_targets": ["archwiki", "wikipedia_en"],
    "endpoints": {
      "search": "/search",
      "open": "/content"
    }
  }
}
```

### Usage

```bash
# Basic research
python -m research.main "How to install Arch Linux?"

# Specify preset (tiny|standard|deep)
python -m research.main "systemd vs openrc" --preset deep

# Adjust verbosity
python -m research.main "python async patterns" --verbosity long
```

### Presets

- **tiny**: 30s, 2 iterations, minimal sources
- **standard**: 2min, 6 iterations, balanced approach  
- **deep**: 5min, 12 iterations, comprehensive research

### Architecture

- **Balanced research**: Each iteration pulls from both Kiwix (grounding) and SearxNG (freshness)
- **Conflict resolution**: Bounded sub-loop chases "changed/deprecated/official" evidence
- **Anti-drowning**: Deep mode may open many sources, but citations are capped
- **Documentation focus**: Writer prompt enforces explanations, not just commands

### Kiwix Endpoint Configuration

Kiwix server setups vary. The adapter supports common patterns:

- Search: `/search?q=...&format=json&zim=...`
- Open: `/content/{zim}/{title}` or `/content?zim=...&title=...`

Adjust `endpoints.search/open` in config to match your kiwix-serve instance.

### Output Format

Natural language explanation with:
- Inline citations like `(S1)` or `(ArchWiki, S2)`
- Final "Sources:" list mapping S# → title + reference
- Clear conflict reporting when issues remain unresolved