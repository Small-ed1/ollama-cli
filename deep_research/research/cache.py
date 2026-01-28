from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any, Optional


class DiskCache:
    """
    Simple file-based cache with TTL.
    Keys map to JSON blobs stored on disk.

    This is intentionally dumb but robust.
    """
    def __init__(self, root: str = ".cache_research"):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path_for_key(self, key: str) -> Path:
        h = hashlib.sha256(key.encode("utf-8")).hexdigest()
        return self.root / f"{h}.json"

    def get(self, key: str, ttl_s: Optional[int] = None) -> Optional[Any]:
        p = self._path_for_key(key)
        if not p.exists():
            return None
        try:
            obj = json.loads(p.read_text(encoding="utf-8"))
            if ttl_s is not None:
                ts = obj.get("_ts", 0)
                if time.time() - ts > ttl_s:
                    return None
            return obj.get("value")
        except Exception:
            return None

    def set(self, key: str, value: Any) -> None:
        p = self._path_for_key(key)
        payload = {"_ts": time.time(), "value": value}
        p.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")