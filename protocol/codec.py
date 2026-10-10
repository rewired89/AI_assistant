"""Wire format: build, encode, decode, validate. Transport-agnostic."""
from __future__ import annotations

import json
import uuid
from functools import lru_cache
from pathlib import Path

from jsonschema import Draft202012Validator

PROTOCOL_VERSION = 1
SUPPORTED_VERSIONS = (1,)
MAX_MESSAGE_BYTES = 16 * 1024

_SCHEMA_DIR = Path(__file__).parent / "schema"


class ProtocolError(Exception):
    """code: malformed | too_large | unsupported_version | auth_failed | replay |
    unknown_type | invalid_payload"""

    def __init__(self, code: str, message: str, message_id: str | None = None):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.message_id = message_id


@lru_cache(maxsize=1)
def _registry() -> dict:
    payloads = json.loads((_SCHEMA_DIR / "payloads.json").read_text())
    defs = payloads["$defs"]
    out = {"command": {}, "event": {}}
    for kind, key in (("command", "commands"), ("event", "events")):
        for mtype, schema in payloads[key].items():
            out[kind][mtype] = Draft202012Validator({**schema, "$defs": defs})
    return out


@lru_cache(maxsize=1)
def _envelope_validator() -> Draft202012Validator:
    return Draft202012Validator(json.loads((_SCHEMA_DIR / "envelope.json").read_text()))


def command_types() -> frozenset[str]:
    return frozenset(_registry()["command"])


def event_types() -> frozenset[str]:
    return frozenset(_registry()["event"])


def new_message_id() -> str:
    return uuid.uuid4().hex


def build(mtype: str, payload: dict | None = None, message_id: str | None = None) -> dict:
    """Unsigned message. Sign with Authenticator.sign() before sending."""
    return {
        "protocol_version": PROTOCOL_VERSION,
        "message_id": message_id or new_message_id(),
        "type": mtype,
        "payload": payload or {},
    }


def encode(msg: dict) -> bytes:
    raw = json.dumps(msg, separators=(",", ":"), sort_keys=True).encode()
    if len(raw) > MAX_MESSAGE_BYTES:
        raise ProtocolError("too_large", f"{len(raw)} > {MAX_MESSAGE_BYTES} bytes")
    return raw


def decode(raw: bytes | str) -> dict:
    """Size check, JSON parse, envelope shape. Does NOT check auth or payload."""
    if isinstance(raw, str):
        raw = raw.encode()
    if len(raw) > MAX_MESSAGE_BYTES:
        raise ProtocolError("too_large", f"{len(raw)} > {MAX_MESSAGE_BYTES} bytes")
    try:
        msg = json.loads(raw)
    except (ValueError, UnicodeDecodeError) as e:
        raise ProtocolError("malformed", f"not JSON: {e}") from None
    if not isinstance(msg, dict):
        raise ProtocolError("malformed", "top level must be an object")
    mid = msg.get("message_id") if isinstance(msg.get("message_id"), str) else None
    errs = sorted(_envelope_validator().iter_errors(msg), key=lambda e: list(e.path))
    if errs:
        raise ProtocolError("malformed", errs[0].message[:120], mid)
    return msg


def check_version(msg: dict) -> None:
    if msg["protocol_version"] not in SUPPORTED_VERSIONS:
        raise ProtocolError(
            "unsupported_version",
            f"version {msg['protocol_version']} not in {list(SUPPORTED_VERSIONS)}",
            msg["message_id"],
        )


def validate_payload(msg: dict, kind: str) -> None:
    """kind: 'command' (host to device) or 'event' (device to host)."""
    validator = _registry()[kind].get(msg["type"])
    if validator is None:
        raise ProtocolError("unknown_type", msg["type"], msg["message_id"])
    errs = sorted(validator.iter_errors(msg["payload"]), key=lambda e: list(e.path))
    if errs:
        raise ProtocolError("invalid_payload", errs[0].message[:120], msg["message_id"])
