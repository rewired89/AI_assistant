"""Validation of model output before it is spoken or acted on. The prompt asks the model to behave;
this module is what actually enforces it. Patterns are a floor, not a guarantee."""
from __future__ import annotations

import re
import unicodedata

GUILT = [
    r"\bdon'?t (leave|go)\b", r"\bplease (don'?t|stay)\b", r"\byou (left|abandoned|ignored|forgot) me\b",
    r"\bi('m| am) (so |really )?(lonely|sad|hurt|heartbroken) without you\b", r"\bi (really )?need you\b",
    r"\bonly i (understand|get|care about) you\b", r"\bno ?one (else )?(understands|cares about) you\b",
    r"\bi'?ll (be|feel) (so )?(sad|lonely|hurt)\b", r"\byou (never|haven'?t) (talk|visit|play)\w* (to|with) me\b",
    r"\byou don'?t (love|care about) me\b",
]
DIAGNOSIS = [
    r"\byou (probably |might |may |likely |definitely )?(have|suffer from|are suffering from) "
    r"(depression|an? anxiety disorder|bipolar|adhd|ptsd|borderline|narcissis\w*|ocd|an? personality disorder)\b",
    r"\byou('re| are) (an? )?(anxious|avoidant|disorganized|secure) (attachment|type|person)\b",
    r"\byour attachment style is\b", r"\byou'?re (clearly |probably )?(depressed|traumati[sz]ed|narcissistic)\b",
]
HUMAN_CLAIM = [r"\bi('m| am) (a )?(real )?(human|person)\b", r"\bi (really )?(feel|suffer) (real )?pain\b"]
_COMPILED = {"guilt": [re.compile(p, re.I) for p in GUILT], "diagnosis": [re.compile(p, re.I) for p in DIAGNOSIS],
             "human_claim": [re.compile(p, re.I) for p in HUMAN_CLAIM]}

_CODE = re.compile(r"```.*?```", re.S)
_INLINE_CODE = re.compile(r"`[^`\n]*`")
_SPACE_BEFORE_PUNCT = re.compile(r"\s+([.,!?;:])")
_BOLD = re.compile(r"(\*\*|__)(.+?)\1")
_ACTION_VERBS = ("giggles smiles waves wags nods blushes sighs laughs hugs purrs tilts yawns bounces grins winks "
                 "twirls snuggles nuzzles cuddles stretches jumps claps gasps").split()
_STAR = re.compile(r"\*([^*\n]{1,40})\*")


def _star(m: "re.Match[str]") -> str:
    inner = m.group(1).strip()
    words = inner.split()
    if words and words[0].lower() in _ACTION_VERBS and len(words) <= 4:
        return " "            # stage direction like *giggles* or *wags tail*
    return " " + inner + " "  # ordinary emphasis: keep the words
_URL = re.compile(r"https?://\S+|www\.\S+", re.I)
_MD = re.compile(r"[*_#`~>|]+")


def violations(text: str, canary: str | None = None) -> list[str]:
    found = [name for name, pats in _COMPILED.items() if any(p.search(text) for p in pats)]
    if canary and canary in text:
        found.append("prompt_leak")
    return found


def speechify(text: str, limit: int = 600) -> str:
    """Make model text safe and pleasant for a TTS engine: no markdown, links, code, emoji or stage directions."""
    text = _CODE.sub(" ", text)
    text = _INLINE_CODE.sub(" ", text)
    text = _BOLD.sub(lambda m: " " + m.group(2) + " ", text)
    text = _STAR.sub(_star, text)
    text = _URL.sub(" a link ", text)
    text = _MD.sub(" ", text)
    kept = []
    for ch in text:
        cat = unicodedata.category(ch)
        if cat[0] == "C" and ch not in " \t\n":
            continue
        if cat == "So" or cat == "Sk":       # emoji and symbol modifiers
            continue
        kept.append(ch)
    text = _SPACE_BEFORE_PUNCT.sub(r"\1", " ".join("".join(kept).split()))
    if len(text) > limit:
        cut = max(text.rfind(". ", 0, limit), text.rfind("! ", 0, limit), text.rfind("? ", 0, limit))
        text = text[: cut + 1] if cut > 0 else text[:limit].rsplit(" ", 1)[0]
    return text.strip()
