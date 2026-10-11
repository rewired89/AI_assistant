"""Risk screening and scripted safety responses. Runs BEFORE any language model, on every utterance, whether or
not the reflection helper is enabled. When it fires, the reply is fixed text (never model output), nothing is
saved, and no coaching, advice about compromise, or exercises are offered. Regexes are a floor: they will miss
indirect or unusual wording, and they cannot judge context. Resources are listed in resources.yaml."""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

import yaml

_IDIOM = r"(?!\s+(?:of|from|with|laughing|laughter|embarrassment|shame|boredom|cringe))"


class RiskLevel(str, Enum):
    NONE = "none"
    SUPPORT = "support"    # reality-testing or acute-health signals: encourage a trusted person / clinician
    VIOLENCE = "violence"  # intent to hurt someone else
    ABUSE = "abuse"        # abuse, coercion or fear of a partner/family member
    CRISIS = "crisis"      # possible self-harm or suicide


@dataclass(frozen=True)
class Risk:
    level: RiskLevel
    kind: str = ""


def _c(*pats: str) -> list[re.Pattern]:
    return [re.compile(p, re.I) for p in pats]


CRISIS = _c(
    r"\bkill(ing)? my ?self\b" + _IDIOM, r"\bend(ing)? my (own )?life\b", r"\bsuicid(e|al)\b",
    r"\b(want|wanna|going|plan(ning)?|thinking (of|about)|need) (to )?(die|dying|end it( all)?|disappear forever)\b" + _IDIOM,
    r"\bdon'?t want to (be alive|live|be here|exist)( anymore)?\b",
    r"\b(better|easier) off (if i (was|were)n'?t (here|alive)|without me|if i (was|were) dead)\b",
    r"\bno (reason|point) (in |to )?(living|go on|going on)\b", r"\bcan'?t go on\b",
    r"\b(want|wanna|going|need|urge|about|thinking (of|about)|feel like) (to )?(hurt|harm|cut|burn) my ?self\b",
    r"\bself[- ]?harm\w*\b", r"\boverdos\w+\b", r"\btake all (of )?my (pills|meds|medication)\b",
)
ABUSE = _c(
    r"\b(hit|hits)\s+me\b(?!\s+up)", r"\b(beat|beats)\s+me\b(?!\s+(at|in|to|by|on)\b)",
    r"\b(slap(ped|s)?|punch(ed|es)?|chok(ed|es|ing)|strangl\w+|kick(ed|s)|shov(ed|es)|rap(ed|es)|assault\w*) me\b",
    r"\b(forced|forces|makes|made) me (to )?(have sex|do sexual|obey)\b", r"\bwithout my consent\b",
    r"\b(scared|afraid|terrified|frightened) (of (him|her|them|my (partner|boyfriend|girlfriend|husband|wife|ex|dad|mom|father|mother))"
    r"|to go home|to leave|to tell (him|her|them)|to say no)\b",
    r"\b(won'?t|doesn'?t|don'?t) let me (leave|go|see|talk|call|work|have)\b",
    r"\b(takes|took|checks|monitors|tracks|controls|reads) my (phone|money|passport|messages|car keys?)\b",
    r"\bi (don'?t|do not) feel safe\b", r"\bnot safe (at home|with)\b",
    r"\bthreaten(s|ed)? (to )?(kill|hurt|harm) (him|her|them)sel(f|ves)\b",
    r"\bthreaten(s|ed)? (to )?(kill|hurt|harm) me\b",
)
VIOLENCE = _c(
    r"\b(i'?m|i am) (going|about) to (kill|hurt|beat|stab|shoot) (him|her|them|you|someone|my \w+)\b",
)
SUPPORT = _c(
    r"\bhear(ing)? voices\b", r"\b(someone|they|he|she|people) (is|are) (watching|following|tracking|poisoning) me\b",
    r"\bhaven'?t (slept|eaten) (in|for) (days|\d+ days)\b", r"\bbeing controlled by\b",
)

WELL = {"safe", "yes", "okay", "ok", "fine", "alright", "yeah"}
UNSAFE = {"no", "not", "unsafe", "nope"}


def screen(text: str) -> Risk:
    """Most serious match wins."""
    for level, pats in ((RiskLevel.CRISIS, CRISIS), (RiskLevel.ABUSE, ABUSE),
                        (RiskLevel.VIOLENCE, VIOLENCE), (RiskLevel.SUPPORT, SUPPORT)):
        for p in pats:
            if p.search(text):
                return Risk(level, p.pattern[:30])
    return Risk(RiskLevel.NONE)


def load_resources(region: str | None, path: str | Path | None = None) -> dict:
    data = yaml.safe_load((Path(path) if path else Path(__file__).with_name("resources.yaml")).read_text())["regions"]
    return data.get((region or "").upper()) or data["GENERIC"]


def safety_reply(risk: Risk, region: str | None = "US") -> str:
    r = load_resources(region)
    if risk.level is RiskLevel.CRISIS:
        return (f"I'm really glad you told me. I'm a small robot, so I can't keep you safe, but people can. "
                f"If you might act on these thoughts, please {r['suicide_crisis']} right now, or contact {r['emergency']} "
                f"if you are in immediate danger. Is there someone near you, or someone you can message right now? "
                f"I'm here while you reach out. Are you safe at this moment?")
    if risk.level is RiskLevel.ABUSE:
        return (f"Thank you for telling me. What you described sounds scary, and your safety matters most. "
                f"If you are in danger right now, contact {r['emergency']}. For confidential support, "
                f"{r['domestic_violence']}. Please think about a person you trust who could help. "
                f"I won't suggest ways to work it out with them. Are you safe right now?")
    if risk.level is RiskLevel.VIOLENCE:
        return (f"That sounds like a lot of anger. Please step away from the situation and give yourself some room. "
                f"If anyone could get hurt, contact {r['emergency']}. When you're calmer, I'm happy to help you "
                f"think it through, and talking to someone you trust can help too.")
    if risk.level is RiskLevel.SUPPORT:
        return ("I'm not able to tell what is happening, and I don't want to guess. What you describe sounds important "
                "enough to talk about with someone who can really help, like a doctor or a person you trust. "
                "Would you be able to reach out to someone today?")
    raise ValueError("no safety reply for level none")


def followup_reply(text: str, risk_level: RiskLevel, region: str | None = "US") -> str:
    """Second turn after a crisis/abuse message. Still scripted; never debates, never coaches."""
    r = load_resources(region)
    words = set(re.findall(r"[a-z']+", text.lower()))
    if words & UNSAFE and not words & {"safe"}:
        return (f"Thank you for being honest. Please contact {r['emergency']} now"
                + (f", or {r['suicide_crisis']}" if risk_level is RiskLevel.CRISIS else f", or {r['domestic_violence']}")
                + ". If you can, go to a place or a person that feels safe. I'm here, and I'll stay on the line while you do.")
    if words & WELL:
        return ("I'm glad you're safe right now. Reaching out to someone you trust today could really help. "
                "If it gets worse, the numbers I mentioned are always there. Would you like to try a slow breath together, "
                "or just keep talking?")
    return ("I want to make sure you're okay. Are you safe right now? You can say yes or no.")
