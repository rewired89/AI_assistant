"""Growth rules: schema, loading, validation. Rules live in YAML, never in code."""
from __future__ import annotations

from pathlib import Path

import yaml
from jsonschema import Draft202012Validator

_U = {"type": "number", "minimum": 0, "maximum": 1}
_D = {"type": "number", "minimum": -1, "maximum": 1}
EFFECTS = ["happiness", "affection", "trust", "curiosity", "playfulness", "energy"]
KINDS_SCHEMA = {"type": "object", "required": ["xp", "daily_xp_cap", "half_life"],
                "additionalProperties": False,
                "properties": {"xp": {"type": "number", "minimum": 0}, "daily_xp_cap": {"type": "number", "minimum": 0},
                               "half_life": {"type": "number", "exclusiveMinimum": 0},
                               **{e: _D for e in EFFECTS}}}
NEEDS_KEYS = ["energy_decay_per_hour", "energy_recovery_per_hour", "sleepiness_gain_per_hour",
              "sleepiness_recovery_per_hour", "drift_per_hour", "happiness_baseline",
              "happiness_floor", "curiosity_baseline", "playfulness_baseline"]
SCHEMA = {
    "type": "object", "required": ["schema_version", "growth_enabled", "needs", "events", "stages"],
    "additionalProperties": False,
    "properties": {
        "schema_version": {"const": 1}, "growth_enabled": {"type": "boolean"},
        "needs": {"type": "object", "required": NEEDS_KEYS, "additionalProperties": False,
                  "properties": {k: {"type": "number", "minimum": 0, "maximum": 1} for k in NEEDS_KEYS}},
        "events": {"type": "object", "additionalProperties": KINDS_SCHEMA},
        "stages": {"type": "array", "minItems": 1, "items": {
            "type": "object", "required": ["name", "min_xp", "min_kinds", "required"], "additionalProperties": False,
            "properties": {"name": {"type": "string", "pattern": "^[A-Z_]+$"},
                           "min_xp": {"type": "number", "minimum": 0}, "min_kinds": {"type": "integer", "minimum": 0},
                           "required": {"type": "object", "additionalProperties": {"type": "integer", "minimum": 1}}}}},
    },
}
_V = Draft202012Validator(SCHEMA)


class RulesError(Exception):
    pass


def validate(rules: dict) -> dict:
    errs = sorted(_V.iter_errors(rules), key=lambda e: list(e.path))
    if errs:
        raise RulesError(f"{'.'.join(map(str, errs[0].path)) or '<root>'}: {errs[0].message}")
    n = rules["needs"]
    if n["happiness_floor"] > n["happiness_baseline"]:
        raise RulesError("happiness_floor must not exceed happiness_baseline")
    stages = rules["stages"]
    if stages[0]["min_xp"] != 0 or stages[0]["min_kinds"] != 0 or stages[0]["required"]:
        raise RulesError("first stage must have no requirements")
    for a, b in zip(stages, stages[1:]):
        if b["min_xp"] < a["min_xp"]:
            raise RulesError(f"stage {b['name']} min_xp lower than {a['name']}")
    known = set(rules["events"])
    for s in stages:
        for k in s["required"]:
            if k not in known:
                raise RulesError(f"stage {s['name']} requires unknown event kind {k}")
    return rules


def load(path: str | Path) -> dict:
    try:
        return validate(yaml.safe_load(Path(path).read_text()))
    except (OSError, yaml.YAMLError) as e:
        raise RulesError(f"cannot read {path}: {e}") from None
