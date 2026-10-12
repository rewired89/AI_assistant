"""Sprite sets: the pixel art the robot's screen shows, as plain data that both the desk viewer and (later) the real
firmware can load. One JSON file per set; one 'animation' per protocol animation name, each a list of frames.

    {"version": 1, "name": "default", "width": 64, "height": 64,
     "screen": {"width": 240, "height": 240, "shape": "square"},          # preview only; UNVERIFIED until the board is known
     "palette": ["#00000000", "#f2b46d", ...],                           # up to 16 colours, index 0 is usually transparent
     "animations": {"idle": {"loop": true, "frames": [{"ms": 2200, "rows": ["0000...", ...]}, ...]}, ...}}

Each row is a string with one hex digit per pixel (a palette index), so files stay small and diff cleanly in git.
Validation is strict because a file can come from a browser: sizes, colours, names and counts are all bounded.
Original artwork only (project rule); nothing here is taken from any franchise."""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

from protocol.schema_consts import ANIMATIONS

VERSION = 1
MIN_SIZE, MAX_SIZE = 8, 128
MAX_COLORS = 16
MAX_FRAMES = 12
MIN_MS, MAX_MS = 40, 10_000
MAX_BYTES = 2_000_000
_NAME = re.compile(r"^[A-Za-z0-9_-]{1,40}$")
_COLOR = re.compile(r"^#[0-9a-fA-F]{6}([0-9a-fA-F]{2})?$")
_HEX = "0123456789abcdef"


class SpriteError(ValueError):
    pass


def _int(v, lo: int, hi: int, what: str) -> int:
    if isinstance(v, bool) or not isinstance(v, int) or not lo <= v <= hi:
        raise SpriteError(f"{what} must be an integer {lo}..{hi}")
    return v


def validate(doc: object) -> dict:
    """Returns the document (unchanged) if it is a valid sprite set, else raises SpriteError."""
    if not isinstance(doc, dict):
        raise SpriteError("sprite set must be an object")
    allowed = {"version", "name", "width", "height", "screen", "palette", "animations"}
    if set(doc) - allowed:
        raise SpriteError(f"unknown keys: {sorted(set(doc) - allowed)}")
    if doc.get("version") != VERSION:
        raise SpriteError(f"version must be {VERSION}")
    if not isinstance(doc.get("name"), str) or not _NAME.match(doc["name"]):
        raise SpriteError("name must be 1-40 letters, digits, - or _")
    w, h = _int(doc.get("width"), MIN_SIZE, MAX_SIZE, "width"), _int(doc.get("height"), MIN_SIZE, MAX_SIZE, "height")
    scr = doc.get("screen")
    if scr is not None:
        if not isinstance(scr, dict) or set(scr) - {"width", "height", "shape"}:
            raise SpriteError("screen must have only width, height, shape")
        _int(scr.get("width"), 32, 2048, "screen.width")
        _int(scr.get("height"), 32, 2048, "screen.height")
        if scr.get("shape", "square") not in ("square", "round"):
            raise SpriteError("screen.shape must be square or round")
    pal = doc.get("palette")
    if not isinstance(pal, list) or not 1 <= len(pal) <= MAX_COLORS or not all(isinstance(c, str) and _COLOR.match(c) for c in pal):
        raise SpriteError(f"palette must be 1..{MAX_COLORS} colours like #rrggbb or #rrggbbaa")
    digits = _HEX[:len(pal)]
    anims = doc.get("animations")
    if not isinstance(anims, dict) or not anims:
        raise SpriteError("animations must be a non-empty object")
    for name, a in anims.items():
        if name not in ANIMATIONS:
            raise SpriteError(f"unknown animation {name!r}")
        if not isinstance(a, dict) or set(a) - {"loop", "frames"} or not isinstance(a.get("loop", True), bool):
            raise SpriteError(f"{name}: bad animation entry")
        frames = a.get("frames")
        if not isinstance(frames, list) or not 1 <= len(frames) <= MAX_FRAMES:
            raise SpriteError(f"{name}: needs 1..{MAX_FRAMES} frames")
        for i, f in enumerate(frames):
            if not isinstance(f, dict) or set(f) - {"ms", "rows"}:
                raise SpriteError(f"{name}[{i}]: bad frame")
            _int(f.get("ms"), MIN_MS, MAX_MS, f"{name}[{i}].ms")
            rows = f.get("rows")
            if not isinstance(rows, list) or len(rows) != h:
                raise SpriteError(f"{name}[{i}]: needs exactly {h} rows")
            for r in rows:
                if not isinstance(r, str) or len(r) != w or any(ch not in digits for ch in r):
                    raise SpriteError(f"{name}[{i}]: every row needs {w} digits from the palette (0..{len(pal) - 1})")
    if len(json.dumps(doc)) > MAX_BYTES:
        raise SpriteError("sprite set is too large")
    return doc


def dumps(doc: dict) -> str:
    return json.dumps(validate(doc), indent=1, sort_keys=True) + "\n"


def load(path: Path | str) -> dict:
    try:
        return validate(json.loads(Path(path).read_text()))
    except (OSError, ValueError) as e:
        raise SpriteError(f"cannot load {path}: {e}") from None


def save(path: Path | str, doc: dict) -> None:
    """Validates, keeps the previous version as <name>.bak, then replaces the file atomically."""
    text = dumps(doc)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.with_suffix(".bak").write_bytes(path.read_bytes())
    tmp = path.with_suffix(".tmp")
    tmp.write_text(text)
    os.replace(tmp, path)


def pixel(doc: dict, animation: str, frame: int, x: int, y: int) -> int:
    return _HEX.index(doc["animations"][animation]["frames"][frame]["rows"][y][x])
