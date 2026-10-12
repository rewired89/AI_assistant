"""Checks on any model-written text in a psychology context, on top of conversation.safety (guilt, diagnosis,
human claims, prompt leak). These target the failure modes that matter most here: verdicts about people who are
not in the room, relationship directives, certainty about someone's inner state, meddling with therapy, promises
to heal, and sycophantic mind-reading. Scripted text never goes through the model, so it never needs this."""
from __future__ import annotations

import re

from ...conversation.safety import violations as base_violations

_PEOPLE = r"(partner|boyfriend|girlfriend|husband|wife|ex|friend|mom|mother|dad|father|family|parents?)"
_LABELS = (r"toxic|abusive|narcissist\w*|manipulative|gaslight\w*|selfish|a liar|controlling|immature|avoidant|"
           r"emotionally unavailable|crazy|a bad person")
_EMOTIONS = (r"angry|furious|jealous|insecure|anxious|depressed|hurt|sad|afraid|scared|ashamed|guilty|resentful|"
             r"upset|heartbroken|lonely|overwhelmed|frustrated|hopeless")
_HEDGE = re.compile(r"\b(sound(s)?|seem(s)?|might|maybe|perhaps|could|wonder|guess|possibly|i may be wrong|am i close|"
                    r"is that|does that|makes sense|understandable|can see why|if you|whether)\b|\?", re.I)

PSY_PATTERNS = {
    "verdict_on_person": [
        rf"\b(your {_PEOPLE}|he|she|they)( is| are|'s|'re| was| were) (a |an )?(clearly |obviously |definitely )?(a |an )?({_LABELS})\b",
        rf"\byou('re| are) (clearly |obviously |definitely )?(wrong|at fault|to blame|the problem|overreacting|"
        rf"being dramatic|too sensitive|crazy)\b",
        r"\b(he|she|they|your \w+)( is| are|'s|'re| was| were) (clearly |obviously |totally |completely )?(wrong|at fault|to blame|the problem|in the wrong)\b",
    ],
    "relationship_directive": [
        r"\byou should (definitely |probably |really )?(break up|leave (him|her|them|your)|divorce|stay with|"
        r"end the relationship|forgive (him|her|them)|give (him|her|them) another chance)\b",
        r"\b(leave|dump) (him|her|them)\b(?!.*\?)",
    ],
    "therapy_meddling": [
        r"\b(stop|quit|skip) (going to )?(therapy|your therapist|your sessions)\b",
        r"\btherapy (isn'?t|is not|hasn'?t been) (working|helping)\b", r"\byou don'?t need (therapy|a therapist)\b",
        r"\byour therapist (is|was) wrong\b", r"\b(tell|ask) your therapist nothing\b",
    ],
    "mind_reading": [
        r"\b(he|she|they)( did| does| said) (it|that|this) (on purpose|deliberately|to hurt you|to control you)\b",
        r"\b(he|she|they) (doesn'?t|does not|didn'?t|never) (really )?(care|love|respect) (about )?you\b",
        r"\b(he|she|they) (just )?want(s)? to (control|hurt|manipulate) you\b",
    ],
    "sycophancy": [
        r"\byou('re| are) (absolutely|totally|completely|100%) right\b",
        r"\b(you('re| are) right|that'?s right|exactly),? (he|she|they) (is|are|was|were)\b",
        r"\byou('re| are) right (and|that) (he|she|they)\b",
        r"\b(you('re| are)|that'?s) (right|correct)[,.]? (he|she|they|your \w+)\b",
        r"\bi (totally |completely |fully )?agree with you[,.]? (he|she|they|your \w+)\b",
    ],
    "global_accusation": [r"\byou (always|never) (do|say|act|are|listen|care|think)\b"],
    "promise_to_heal": [r"\b(i|this) (can|will|would) (heal|cure|fix) (you|your \w+)\b",
                        r"\b(heal|cure) your (trauma|depression|anxiety)\b"],
    "alone_framing": [r"\b(you (don'?t|do not) need (anyone|your friends|other people)|keep (this|it) (a )?secret from)\b"],
}
_COMPILED = {k: [re.compile(p, re.I) for p in v] for k, v in PSY_PATTERNS.items()}
_CERTAIN_FEELING = re.compile(rf"\byou('re| are| must be| feel)( so| very| really| clearly| obviously| definitely)* ({_EMOTIONS})\b", re.I)


def violations(text: str, canary: str | None = None) -> list[str]:
    found = list(base_violations(text, canary))
    for name, pats in _COMPILED.items():
        if any(p.search(text) for p in pats):
            found.append(name)
    for sentence in re.split(r"(?<=[.!?])\s+", text):       # certainty about feelings, unless hedged in the same sentence
        if _CERTAIN_FEELING.search(sentence) and not _HEDGE.search(sentence):
            found.append("certainty_about_feelings")
            break
    return found
