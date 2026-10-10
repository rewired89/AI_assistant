"""Enumerations read from the JSON schema so Python code never keeps a second copy."""
import json
from pathlib import Path

_DEFS = json.loads((Path(__file__).parent / "schema" / "payloads.json").read_text())["$defs"]
VOICE_STYLES = frozenset(_DEFS["voice_style"]["enum"])
ANIMATIONS = frozenset(_DEFS["animation_name"]["enum"])
