"""Spots a wake phrase ("hey girl") at the start of a transcript. Whisper spells names and short words loosely, so each
word is compared with a similarity threshold instead of exactly. Returns what was said after the phrase, or None."""
from __future__ import annotations

import difflib
import re

_WORD = re.compile(r"[a-z0-9']+")
FILLERS = {"um", "uh", "oh", "ok", "okay", "so", "well"}


def match_wake(text: str, phrase: str, threshold: float = 0.66) -> str | None:
    target = _WORD.findall(phrase.lower())
    if not target:
        return None
    low = text.lower()
    spans = list(_WORD.finditer(low))
    for skip in (0, 1):
        if len(spans) < skip + len(target):
            break
        if skip and spans[0].group() not in FILLERS:
            break
        head = spans[skip:skip + len(target)]
        if all(difflib.SequenceMatcher(None, m.group(), t).ratio() >= threshold for m, t in zip(head, target)):
            return text[head[-1].end():].lstrip(" ,.!?;:-—").strip()
    return None
