# Legacy Ollama CLI

**This file is archived and deprecated. Please use the main `ollama_cli.py` entry point instead.**

This legacy version contains the original monolithic implementation with all functionality in a single file. It has been replaced by the modular implementation in the `src/` directory.

## Migration

To use the current version:

```bash
# Main entry point (recommended)
python3 ollama_cli.py --help

# Or via module
python3 -m src.cli --help
```

## Removal

This legacy file will be removed in a future version. Please update any scripts or documentation that reference it.