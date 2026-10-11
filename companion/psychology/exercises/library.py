"""Loads and validates the exercise library against the evidence register."""
from __future__ import annotations

import string
from pathlib import Path

import yaml

from ..sources import load_sources

REQUIRED = ("exercise_id", "name", "kind", "intended_skill", "source_or_evidence_basis", "when_to_offer",
            "when_not_to_offer", "estimated_duration", "instructions", "steps", "reflection_questions",
            "privacy_behavior", "success_criteria", "limitations")
KINDS = {"scripted", "conflict_guide", "growth_plan"}
STEP_KINDS = {"say", "ask_text", "ask_choice", "ask_scale", "ask_yesno", "ask_time", "build_text"}


class LibraryError(ValueError):
    pass


def validate_exercise(ex: dict, sources: dict) -> None:
    eid = ex.get("exercise_id", "?")
    missing = [k for k in REQUIRED if k not in ex]
    if missing:
        raise LibraryError(f"{eid}: missing {missing}")
    if ex["kind"] not in KINDS:
        raise LibraryError(f"{eid}: bad kind")
    for k in ("when_to_offer", "when_not_to_offer", "success_criteria", "limitations", "source_or_evidence_basis"):
        if not ex[k] or not all(isinstance(x, str) and x.strip() for x in ex[k]):
            raise LibraryError(f"{eid}: {k} must be a non-empty list of strings")
    for s in ex["source_or_evidence_basis"]:
        if s not in sources:
            raise LibraryError(f"{eid}: unknown source {s}")
    if not any("safety" in w.lower() or "abuse" in w.lower() or "self-harm" in w.lower() for w in ex["when_not_to_offer"]):
        raise LibraryError(f"{eid}: when_not_to_offer must mention safety signals")
    if any("agree" in c.lower() and "disagree" not in c.lower() for c in ex["success_criteria"]):
        raise LibraryError(f"{eid}: success must never mean agreeing with the robot or anyone else")
    if ex["kind"] == "scripted" and not ex["steps"]:
        raise LibraryError(f"{eid}: scripted exercise needs steps")
    seen: set[str] = set()
    for st in ex["steps"]:
        if st.get("kind") not in STEP_KINDS or not st.get("id") or st["id"] in seen:
            raise LibraryError(f"{eid}: bad or duplicate step {st.get('id')}")
        if st["kind"] == "say" and not st.get("text"):
            raise LibraryError(f"{eid}.{st['id']}: say needs text")
        if st["kind"].startswith("ask") and not st.get("prompt"):
            raise LibraryError(f"{eid}.{st['id']}: ask needs prompt")
        if st["kind"] == "ask_choice" and not st.get("options"):
            raise LibraryError(f"{eid}.{st['id']}: choice needs options")
        if st["kind"] == "build_text":
            for _, field, _, _ in string.Formatter().parse(st["template"]):
                if field and field not in seen:
                    raise LibraryError(f"{eid}.{st['id']}: template uses unknown earlier step {field}")
        seen.add(st["id"])


def load_library(path: str | Path | None = None, sources: dict | None = None) -> dict[str, dict]:
    sources = sources or load_sources()
    data = yaml.safe_load((Path(path) if path else Path(__file__).with_name("library.yaml")).read_text())["exercises"]
    out: dict[str, dict] = {}
    for ex in data:
        validate_exercise(ex, sources)
        if ex["exercise_id"] in out:
            raise LibraryError(f"duplicate {ex['exercise_id']}")
        out[ex["exercise_id"]] = ex
    return out
