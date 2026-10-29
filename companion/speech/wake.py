"""Spots a wake phrase ("hey girl") at the start of a transcript. Whisper spells short words loosely and people add
pet names ("hey baby girl"), so words are compared by similarity, the greeting word may be any usual greeting, and up to
two extra words may sit between the phrase's words. Returns what was said after the phrase, or None."""
from __future__ import annotations

import difflib
import re

_WORD = re.compile(r"[a-z0-9']+")
FILLERS = {"um", "uh", "oh", "ok", "okay", "so", "well"}
GREETINGS = {"hey", "hay", "hi", "hello", "hiya", "yo", "bye"}   # "bye": how Whisper often writes a quick "hey"


def _sim(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, a, b).ratio()


def match_wake(text: str, phrase: str, threshold: float = 0.66, max_gap: int = 2) -> str | None:
    target = _WORD.findall(phrase.lower())
    if not target:
        return None
    spans = list(_WORD.finditer(text.lower()))
    for skip in (0, 1):
        if skip and (not spans or spans[0].group() not in FILLERS):
            break
        if len(spans) < skip + len(target):
            break
        first = spans[skip].group()
        if not (first in GREETINGS if target[0] in GREETINGS else _sim(first, target[0]) >= threshold):
            continue
        j, ok = skip, True
        for t in target[1:]:
            found = next((k for k in range(j + 1, min(len(spans), j + 2 + max_gap)) if _sim(spans[k].group(), t) >= threshold), None)
            if found is None:
                ok = False
                break
            j = found
        if ok:
            return text[spans[j].end():].lstrip(" ,.!?;:-—").strip()
    return None
