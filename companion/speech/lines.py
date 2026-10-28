"""Scripted lines the pipeline speaks about its own state. They never depend on the LLM, so
they work when the model is down, and they never claim more than the code knows."""
from __future__ import annotations

from pathlib import Path

import yaml

DEFAULT_LINES = {
    "didnt_hear": "Sorry, I didn't catch that.",
    "trouble_hearing": "I'm having trouble hearing right now.",
    "trouble_thinking": "My thoughts got tangled. Could you say that again?",
    "battery_low": "My battery is low, so I can't listen right now.",
    "mic_muted": "My microphone is muted, so I can't listen.",
    "wake_ack": "Yes?",
}


def load_lines(species_dir: str | Path | None) -> dict[str, str]:
    lines = dict(DEFAULT_LINES)
    if species_dir:
        f = Path(species_dir) / "speech.yaml"
        if f.exists():
            for k, v in (yaml.safe_load(f.read_text()) or {}).get("lines", {}).items():
                if k not in DEFAULT_LINES or not isinstance(v, str) or not v.strip() or len(v) > 200:
                    raise ValueError(f"bad speech line {k!r}")
                lines[k] = v.strip()
    return lines
