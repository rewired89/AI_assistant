"""Scripted lines for each behavior Intent: the fallback when the model is down or breaks a rule, and the
whole of proactive speech if no model is configured. Species files override (species/*/dialogue.yaml).

Placeholders: {name} (her name, or "friend"), {fact} (a memory she shared), {stage_word}. Nothing here may
assert what she did, where she was, or how long she has been away."""
from __future__ import annotations

import random
import string
from pathlib import Path

import yaml

from ..behavior.engine import INTENT_KINDS

ALLOWED_FIELDS = {"name", "fact", "stage_word"}
STAGE_WORDS = {"BABY": "baby fox", "YOUNG": "young fox", "NINE_TAILS": "nine-tailed fox",
               "PERSONALIZED_NINE_TAILS": "nine-tailed fox who knows you"}
DEFAULTS: dict = {
    "greet": {"new": ["Hello! I'm happy to meet you.", "Hi there!"],
              "familiar": ["Hi {name}! Good to see you.", "Hello {name}!"],
              "close": ["Hi {name}! I'm so happy you're here.", "{name}! Hello hello!"]},
    "greet_after_battery_saving": ["I'm awake! I took a little nap to save my battery."],
    "curious_question": ["I'm curious, what's something you enjoy?", "Can I ask you something? What's a thing you like to do?",
                         "What's something fun you could teach me?"],
    "play_invite": ["Want to play something together?", "I feel playful. Want to play a little game?"],
    "share_interest": ["I remembered something you told me: {fact}. Want to tell me more?"],
    "offer_save_battery": ["My battery is getting low. Want me to take a nap to save power?"],
    "milestone": ["I feel like I grew a little! I'm a {stage_word} now."],
    "comfort_touch_reply": ["Hehe, that feels nice!", "Mmm, thank you!"],
    "followup_due": ["It's the time you planned to come back to something. Want to revisit it now? You can say yes or no."],
}


class DialogueError(ValueError):
    pass


def _check(template: str) -> None:
    for _, field, _, _ in string.Formatter().parse(template):
        if field is not None and field not in ALLOWED_FIELDS:
            raise DialogueError(f"unknown placeholder {{{field}}}")
    if not template.strip() or len(template) > 200:
        raise DialogueError("template must be 1-200 characters")


def load_templates(species_dir: str | Path | None) -> dict:
    out = {k: (dict(v) if isinstance(v, dict) else list(v)) for k, v in DEFAULTS.items()}
    f = Path(species_dir) / "dialogue.yaml" if species_dir else None
    if f and f.exists():
        for kind, val in (yaml.safe_load(f.read_text()) or {}).get("intents", {}).items():
            if kind not in INTENT_KINDS or kind not in out:
                raise DialogueError(f"unknown intent kind {kind!r}")
            if isinstance(out[kind], dict):
                if not isinstance(val, dict) or set(val) - {"new", "familiar", "close"}:
                    raise DialogueError(f"{kind} needs new/familiar/close lists")
                for rel, lines in val.items():
                    out[kind][rel] = _lines(lines)
            else:
                out[kind] = _lines(val)
    return out


def _lines(val) -> list[str]:
    if not isinstance(val, list) or not val or not all(isinstance(t, str) for t in val):
        raise DialogueError("lines must be a non-empty list of strings")
    for t in val:
        _check(t)
    return list(val)


class LinePicker:
    """Picks a template, avoiding an immediate repeat of the last one for that kind."""

    def __init__(self, templates: dict, rng: random.Random | None = None):
        self.t, self.rng, self._last = templates, rng or random.Random(), {}

    def pick(self, kind: str, relationship: str = "new") -> str:
        pool = self.t[kind]
        pool = pool.get(relationship) or pool["new"] if isinstance(pool, dict) else pool
        choices = [x for x in pool if x != self._last.get(kind)] or pool
        line = self.rng.choice(choices)
        self._last[kind] = line
        return line
