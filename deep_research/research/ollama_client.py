from __future__ import annotations

import json
from typing import Any, Dict, List, Optional
import requests


class OllamaClient:
    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")

    def chat(
        self,
        model: str,
        messages: List[Dict[str, Any]],
        options: Optional[Dict[str, Any]] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": False,
        }
        if options:
            payload["options"] = options
        if tools:
            payload["tools"] = tools

        r = requests.post(f"{self.base_url}/api/chat", json=payload, timeout=120)
        r.raise_for_status()
        return r.json()

    @staticmethod
    def content(resp: Dict[str, Any]) -> str:
        msg = resp.get("message", {}) or {}
        return msg.get("content") or ""

    @staticmethod
    def tool_calls(resp: Dict[str, Any]) -> List[Dict[str, Any]]:
        msg = resp.get("message", {}) or {}
        return msg.get("tool_calls") or []