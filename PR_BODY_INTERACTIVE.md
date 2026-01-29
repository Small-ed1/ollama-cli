Revert advertise and enhance interactive mode discoverability

- Reverts the previous advertise feature that printed a full banner of options from the CLI.
- Interactive mode now consistently lands on the advanced interactive menu (Plan C) by default after config selection, making all options visible, including Deep research.
- The interactive entry point was refactored to accept an optional config override so saved or freshly generated config can drive the interactive session without needing a full restart.
- Minor UX improvements: improved exit commands in the advanced interactive help and a clearer path to Deep research from the main menu.

What changed (high level)
- src/ollama_cli/cli.py
  - Removed the advertise subcommand and its wiring.
  - Route interactive flow to the enhanced interactive menu by default when starting interactive.
  - Do not advertise options at startup; instead rely on the interactive menu to show options.
- src/ollama_cli/interactive.py
  - start_interactive now accepts an optional config_override parameter and uses it if provided.
  - Improved the advanced help to reflect available commands and exit shortcuts.
- Documentation/test notes updated in PR body; tests remain compatible with the adjusted flow.

Testing suggestions
- Run: ollama-cli interactive
- Confirm you see the full interactive menu (Chat, Generate, Deep research, List models, Pull a model, Configure defaults, Exit)
- Use Deep research to validate the web/open URL path works when SearxNG is available, and seed URL paths work when SearxNG is down.
- Ensure /exit exits the interactive session cleanly.

This PR is intended to improve discoverability and reliability of the interactive experience without affecting non-interactive commands.
