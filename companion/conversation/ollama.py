"""Local language model via Ollama's HTTP API (https://ollama.com), stdlib only.

Local-first on purpose: by default the endpoint must be loopback or a private-network address, so
conversations cannot be sent to a third party by a typo in a config file. Remote hosts need allow_remote=True.
Tested against a local stub that mimics the documented /api/chat response shape, not against a real Ollama.
"""
from __future__ import annotations

import ipaddress
import json
import socket
import urllib.error
import urllib.request
from urllib.parse import urlparse

from ..interfaces import LanguageModel
from .engine import LLMUnavailable

MAX_RESPONSE_BYTES = 1_000_000


def _is_local(host: str) -> bool:
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return False
    for info in infos:
        ip = ipaddress.ip_address(info[4][0].split("%")[0])
        if not (ip.is_loopback or ip.is_private or ip.is_link_local):
            return False
    return bool(infos)


class OllamaLanguageModel(LanguageModel):
    def __init__(self, model: str, base_url: str = "http://127.0.0.1:11434", timeout_s: float = 60.0,
                 temperature: float = 0.7, max_tokens: int = 300, allow_remote: bool = False):
        u = urlparse(base_url)
        if u.scheme not in ("http", "https") or not u.hostname:
            raise ValueError("base_url must be http(s)://host[:port]")
        if not allow_remote and not _is_local(u.hostname):
            raise ValueError(f"{u.hostname} is not a local/private address; pass allow_remote=True to send conversations off-network")
        self.model, self.base, self.timeout = model, base_url.rstrip("/"), timeout_s
        self.temperature, self.max_tokens = temperature, max_tokens
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))   # never via env proxies

    def complete(self, system: str, messages: list[dict], tools: list[dict] | None = None) -> dict:
        body = {"model": self.model, "stream": False,
                "messages": [{"role": "system", "content": system}, *messages],
                "options": {"temperature": self.temperature, "num_predict": self.max_tokens}}
        if tools:
            body["tools"] = [{"type": "function", "function": t} for t in tools]
        req = urllib.request.Request(self.base + "/api/chat", data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"}, method="POST")
        try:
            with self._opener.open(req, timeout=self.timeout) as resp:
                raw = resp.read(MAX_RESPONSE_BYTES + 1)
            if len(raw) > MAX_RESPONSE_BYTES:
                raise LLMUnavailable("response too large")
            msg = json.loads(raw)["message"]
        except LLMUnavailable:
            raise
        except (urllib.error.URLError, OSError, TimeoutError, ValueError, KeyError, TypeError) as e:
            raise LLMUnavailable(type(e).__name__) from None
        calls = []
        for c in msg.get("tool_calls") or []:
            fn = c.get("function", {})
            args = fn.get("arguments") or {}
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except ValueError:
                    args = {}
            calls.append({"name": fn.get("name", ""), "arguments": args})
        content = msg.get("content") or ""
        return {"text": content if isinstance(content, str) else "", "tool_calls": calls}
