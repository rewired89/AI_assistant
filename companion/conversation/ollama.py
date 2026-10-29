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
    supports_stream = True

    def __init__(self, model: str, base_url: str = "http://127.0.0.1:11434", timeout_s: float = 60.0,
                 temperature: float = 0.7, max_tokens: int = 100, allow_remote: bool = False):
        u = urlparse(base_url)
        if u.scheme not in ("http", "https") or not u.hostname:
            raise ValueError("base_url must be http(s)://host[:port]")
        if not allow_remote and not _is_local(u.hostname):
            raise ValueError(f"{u.hostname} is not a local/private address; pass allow_remote=True to send conversations off-network")
        self.is_local = _is_local(u.hostname)
        self.model, self.base, self.timeout = model, base_url.rstrip("/"), timeout_s
        self.temperature, self.max_tokens = temperature, max_tokens
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))   # never via env proxies

    def complete(self, system: str, messages: list[dict], tools: list[dict] | None = None, on_text=None) -> dict:
        stream = on_text is not None
        body = {"model": self.model, "stream": stream, "keep_alive": "30m",     # keep the model loaded: no reload delay between turns
                "messages": [{"role": "system", "content": system}, *messages],
                "options": {"temperature": self.temperature, "num_predict": self.max_tokens, "num_ctx": 2048}}
        if tools:
            body["tools"] = [{"type": "function", "function": t} for t in tools]
        req = urllib.request.Request(self.base + "/api/chat", data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"}, method="POST")
        try:
            with self._opener.open(req, timeout=self.timeout) as resp:
                if not stream:
                    raw = resp.read(MAX_RESPONSE_BYTES + 1)
                    if len(raw) > MAX_RESPONSE_BYTES:
                        raise LLMUnavailable("response too large")
                    msg = json.loads(raw)["message"]
                else:
                    msg, total = {"content": "", "tool_calls": []}, 0
                    for line in resp:                       # one JSON object per line, each with the next piece of text
                        total += len(line)
                        if total > MAX_RESPONSE_BYTES:
                            raise LLMUnavailable("response too large")
                        if not line.strip():
                            continue
                        part = json.loads(line)
                        m = part.get("message") or {}
                        piece = m.get("content") or ""
                        if isinstance(piece, str) and piece:
                            msg["content"] += piece
                            try:
                                on_text(piece)
                            except Exception:
                                pass                         # a listener's problem never breaks the reply
                        msg["tool_calls"] += m.get("tool_calls") or []
                        if part.get("done"):
                            break
        except LLMUnavailable:
            raise
        except urllib.error.HTTPError as e:
            if stream and e.code == 400:                   # an older Ollama may refuse streaming together with tools: ask the plain way
                return self.complete(system, messages, tools)
            raise LLMUnavailable(type(e).__name__) from None
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
