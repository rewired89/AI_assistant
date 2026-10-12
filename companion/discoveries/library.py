"""The Daily Psychology Discoveries content library (spec 53): one YAML file per entry, validated before anything is spoken.

An entry may be DELIVERED only when its verification status is "verified": it names a real researcher and year, a source
title and an https link, states its limitations, and carries a record of who checked what and when. Everything else stays
in the library as a draft. The wording of the scientific claims lives in the entry; the code never writes claims.

Verification here means: bibliographic details were checked against an independent source and the stated finding does not
go beyond what that source's abstract says. It is NOT a substitute for the owner reading the paper."""
from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

CATEGORIES = ("habits_and_behavior_change", "self_awareness", "emotional_regulation", "openness_to_learning",
              "resilience_and_adaptation", "relationships_and_conflict", "goals_and_personal_systems", "identity_and_growth")
EVIDENCE = ("original_study", "research_review", "meta_analysis", "theory")
STATUSES = ("unverified", "verified")
WORDS_PER_SECOND = 2.5                       # relaxed speech, about 150 words a minute
MIN_SECONDS, MAX_SECONDS = 30, 90
REQUIRED = ("id", "topic", "title", "hook", "researcher", "publication_year", "source_title", "source_url", "evidence_type",
            "summary", "limitations", "everyday_example", "reflection_question", "optional_exercise",
            "estimated_duration_seconds", "sensitive_topic", "verification")
TEXT_FIELDS = ("hook", "summary", "limitations", "everyday_example", "reflection_question", "optional_exercise")

# Language the feature must never use (spec 45, 50, 51): guilt, diagnosis, pressure, singling her out, partner framing.
FORBIDDEN = re.compile(
    r"\byou (always|never)\b|\byou (should|must|need to|have to)\b|\bwhat you do wrong\b|\bexactly what you\b|\bshame\b|\bguilt\b|"
    r"\b(disorder|diagnos\w*|pathology|toxic|narcissis\w*|immature|stubborn|avoidant|broken)\b|\byour (boyfriend|partner|girlfriend|husband)\b|"
    r"\bhe thinks\b|\bscore\b|\bmaturity\b", re.I)
_ID = re.compile(r"^[a-z0-9_]{3,60}$")


class LibraryError(Exception):
    pass


@dataclass
class Entry:
    id: str
    topic: str
    title: str
    hook: str
    researcher: str
    publication_year: int
    source_title: str
    source_url: str
    evidence_type: str
    summary: str
    limitations: str
    everyday_example: str
    reflection_question: str
    optional_exercise: str
    estimated_duration_seconds: int
    sensitive_topic: bool
    verification: dict
    keywords: list = field(default_factory=list)
    raw: dict = field(default_factory=dict, repr=False)

    @property
    def verified(self) -> bool:
        return self.verification.get("status") == "verified"

    @property
    def owner_confirmed(self) -> bool:
        return bool(self.verification.get("owner_confirmed"))

    def sources_text(self) -> str:
        return f"{self.researcher}, {self.publication_year}. {self.source_title}."


def script_for(e: Entry) -> str:
    """The spoken/written form, in the structure of spec 46: hook, researcher, discovery, application, reflection, next step."""
    verb = {"original_study": "studied this in", "research_review": "wrote a review about this in",
            "meta_analysis": "combined many studies on this in", "theory": "described this idea in"}[e.evidence_type]
    parts = [f"Hey! Today's psychology discovery is called {e.title}.", e.hook,
             f"{e.researcher} {verb} {e.publication_year}.", e.summary, e.limitations,
             f"Here's why that's interesting: {e.everyday_example}", f"It makes you wonder: {e.reflection_question}",
             "You don't have to answer now. Just something interesting to think about.",
             "If you're curious, I can tell you where this comes from, or give you a tiny exercise. Or say skip."]
    return " ".join(p.strip() for p in parts)


def seconds_for(text: str) -> float:
    return len(text.split()) / WORDS_PER_SECOND


def problems(d: dict, today: dt.date | None = None) -> list[str]:
    """Everything wrong with one entry (empty list = fine). A draft may be incomplete only where its status says unverified."""
    today = today or dt.date.today()
    out: list[str] = []
    for k in REQUIRED:
        if k not in d:
            out.append(f"missing {k}")
    if out:
        return out
    if not isinstance(d["id"], str) or not _ID.match(d["id"]):
        out.append("id must be lowercase letters, digits and _")
    if d["topic"] not in CATEGORIES:
        out.append(f"topic must be one of {CATEGORIES}")
    if d["evidence_type"] not in EVIDENCE:
        out.append(f"evidence_type must be one of {EVIDENCE}")
    if not isinstance(d["sensitive_topic"], bool):
        out.append("sensitive_topic must be true or false")
    v = d["verification"]
    if not isinstance(v, dict) or v.get("status") not in STATUSES:
        return out + [f"verification.status must be one of {STATUSES}"]
    for k in TEXT_FIELDS + ("title",):
        if not isinstance(d[k], str):
            out.append(f"{k} must be text")
    if out:
        return out
    for k in TEXT_FIELDS + ("title",):
        m = FORBIDDEN.search(d[k]) if d[k] else None
        if m:
            out.append(f"{k} uses language this feature must not use: '{m.group(0)}'")
    if d["reflection_question"] and not d["reflection_question"].strip().endswith("?"):
        out.append("reflection_question must be a question")
    if v["status"] == "verified":
        if not str(d["researcher"]).strip():
            out.append("verified entry needs a named researcher")
        y = d["publication_year"]
        if not isinstance(y, int) or not 1850 <= y <= today.year:
            out.append("verified entry needs a real publication_year")
        if not str(d["source_title"]).strip():
            out.append("verified entry needs a source_title")
        if not str(d["source_url"]).startswith("https://"):
            out.append("verified entry needs an https source_url")
        for k in TEXT_FIELDS:
            if not d[k].strip():
                out.append(f"verified entry needs {k}")
        for k in ("checked_on", "checked_by", "basis"):
            if not str(v.get(k, "")).strip():
                out.append(f"verification.{k} is required for a verified entry")
        try:
            dt.date.fromisoformat(str(v.get("checked_on")))
        except ValueError:
            out.append("verification.checked_on must be a date (YYYY-MM-DD)")
        if not out:
            e = _build(d)
            secs = seconds_for(script_for(e))
            if not MIN_SECONDS <= secs <= MAX_SECONDS:
                out.append(f"spoken length is about {secs:.0f} s; it must be {MIN_SECONDS} to {MAX_SECONDS} s")
            if not MIN_SECONDS <= int(d["estimated_duration_seconds"]) <= MAX_SECONDS:
                out.append("estimated_duration_seconds must be 30 to 90")
    return out


def _build(d: dict) -> Entry:
    return Entry(id=d["id"], topic=d["topic"], title=d["title"], hook=d["hook"], researcher=str(d["researcher"]),
                 publication_year=d["publication_year"] if isinstance(d["publication_year"], int) else 0,
                 source_title=str(d["source_title"]), source_url=str(d["source_url"]), evidence_type=d["evidence_type"],
                 summary=d["summary"], limitations=d["limitations"], everyday_example=d["everyday_example"],
                 reflection_question=d["reflection_question"], optional_exercise=d["optional_exercise"],
                 estimated_duration_seconds=int(d["estimated_duration_seconds"]), sensitive_topic=d["sensitive_topic"],
                 verification=dict(d["verification"]), keywords=[str(k).lower() for k in d.get("keywords") or []], raw=d)


def load_dir(path: str | Path, today: dt.date | None = None) -> tuple[list[Entry], dict[str, list[str]]]:
    """Returns (entries that are valid, {file: problems} for the rest). Invalid entries are never deliverable."""
    entries, bad, seen = [], {}, set()
    for f in sorted(Path(path).glob("*.yaml")):
        try:
            d = yaml.safe_load(f.read_text())
        except (OSError, yaml.YAMLError) as e:
            bad[f.name] = [f"cannot read: {type(e).__name__}"]
            continue
        if not isinstance(d, dict):
            bad[f.name] = ["not a mapping"]
            continue
        p = problems(d, today)
        if not p and d["id"] in seen:
            p = ["duplicate id"]
        if p:
            bad[f.name] = p
            continue
        seen.add(d["id"])
        entries.append(_build(d))
    return entries, bad
