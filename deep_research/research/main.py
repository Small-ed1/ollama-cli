from __future__ import annotations

import argparse
from .config import load_config, default_parameters, PRESETS
from .cache import DiskCache
from .searxng import SearxNG
from .kiwix import KiwixHTTP
from .ollama_client import OllamaClient
from .types import ResearchState
from .orchestrator import Orchestrator


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("query", help="Research query")
    ap.add_argument("--config", default="config.json", help="Path to config.json")
    ap.add_argument("--preset", default="standard", choices=list(PRESETS.keys()))
    ap.add_argument("--verbosity", default=None, choices=["short", "normal", "long"])
    args = ap.parse_args()

    cfg = load_config(args.config)
    params = default_parameters(cfg)
    if args.verbosity:
        params.verbosity = args.verbosity

    budgets = PRESETS[args.preset]

    cache = DiskCache()

    searx = SearxNG(
        base_url=cfg.searxng["base_url"],
        cache=cache,
        safesearch=int(cfg.searxng.get("safesearch", 1)),
    )
    kiwix = KiwixHTTP(
        base_url=cfg.kiwix["base_url"],
        endpoints=cfg.kiwix.get("endpoints", {"search": "/search", "open": "/content"}),
        cache=cache,
    )
    ollama = OllamaClient(cfg.ollama["base_url"])

    models = cfg.ollama.get("models", {})
    options = cfg.ollama.get("options", {})

    state = ResearchState(
        query_original=args.query,
        query_clarified=args.query,
        parameters=params,
        budgets=budgets,
    )

    orch = Orchestrator(
        ollama=ollama,
        searx=searx,
        kiwix=kiwix,
        models=models,
        options=options,
    )

    out = orch.run(state)
    print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())