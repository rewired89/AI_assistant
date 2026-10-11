"""Text clean-up applied BEFORE safety patterns run. Speech-to-text and language models routinely produce curly
apostrophes, odd spaces, zero-width characters and spaced-out letters; the safety patterns are written for plain text,
so everything is brought to plain text first. This only ever makes matching MORE likely (never less)."""
from __future__ import annotations

import re
import unicodedata

_APOS = str.maketrans({"\u2019": "'", "\u2018": "'", "\u02bc": "'", "\u0060": "'", "\u00b4": "'", "\u2032": "'"})
_ZERO = re.compile("[\u200b\u200c\u200d\u2060\ufeff\u00ad]")
_SPACED = re.compile(r"\b(?:[A-Za-z][ .\-_]){3,}[A-Za-z]\b")      # "k i l l", "k.i.l.l"


def normalize(text: str) -> str:
    t = unicodedata.normalize("NFKC", text).translate(_APOS)
    t = _ZERO.sub("", t)
    t = _SPACED.sub(lambda m: re.sub(r"[ .\-_]", "", m.group(0)), t)
    return " ".join(t.split())
