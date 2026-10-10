"""Rule-based candidate extraction and the save/ask/drop policy. An LLM extractor can replace
`extract_candidates` later; `decide` stays the gatekeeper either way."""
from __future__ import annotations

import re

SENSITIVE = re.compile(
    r"\b(diagnos\w*|therap\w*|depress\w*|anxi\w*|trauma\w*|abus\w*|suicid\w*|self[- ]harm|medicat\w*|"
    r"panic|bipolar|adhd|ptsd|eating disorder|assault\w*)\b", re.I)
_PATTERNS = [
    (re.compile(r"\bremember (?:that )?(.{3,200})", re.I), "semantic", None),
    (re.compile(r"\bmy name is ([A-Za-z][\w'-]{0,30})", re.I), "profile", "user_name"),
    (re.compile(r"\bcall me ([A-Za-z][\w'-]{0,30})", re.I), "profile", "preferred_name"),
    (re.compile(r"\bmy favou?rite (\w[\w ]{0,30}?) is (.{1,80})", re.I), "profile", None),
    (re.compile(r"\bi (?:really )?(?:love|like|enjoy) (.{2,80})", re.I), "profile", "likes"),
]


def extract_candidates(utterance: str) -> list[dict]:
    out = []
    text = utterance.strip().rstrip(".!?")
    for pat, cat, key in _PATTERNS:
        m = pat.search(text)
        if not m:
            continue
        if m.re.pattern.startswith(r"\bmy favou"):
            out.append({"category": cat, "key": f"favorite_{m.group(1).strip().lower().replace(' ', '_')}",
                        "value": m.group(2).strip()})
        else:
            out.append({"category": cat, "key": key, "value": m.group(1).strip()})
        out[-1].update(source="explicit_user_statement", confidence=1.0, source_context=utterance[:200])
    for c in out:
        c["sensitive"] = bool(SENSITIVE.search(c["value"]))
    return out


def decide(candidate: dict) -> str:
    """'save' | 'ask' | 'drop'. Never saves inferences or sensitive content silently."""
    if not candidate.get("value"):
        return "drop"
    if candidate.get("source") == "ai_inference":
        return "ask"
    if candidate.get("sensitive"):
        return "ask"
    return "save"
