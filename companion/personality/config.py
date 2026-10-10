"""Personality configuration: schema, validation, safe updates, bounded trait adaptation."""
from __future__ import annotations

import copy
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator

_UNIT = {"type": "number", "minimum": 0, "maximum": 1}
_TRAITS = ["affectionate", "curious", "playful", "protective", "mischievous", "calm", "assertive"]

SCHEMA = {
    "type": "object",
    "required": ["character", "traits", "communication", "behavior"],
    "additionalProperties": False,
    "properties": {
        "character": {"type": "object", "required": ["name", "species", "personality_profile"],
                      "additionalProperties": False,
                      "properties": {"name": {"type": "string", "minLength": 1, "maxLength": 32},
                                     "species": {"type": "string", "minLength": 1, "maxLength": 32},
                                     "personality_profile": {"type": "string", "pattern": "^[a-z_]+$"}}},
        "traits": {"type": "object", "required": _TRAITS, "additionalProperties": False,
                   "properties": {t: _UNIT for t in _TRAITS}},
        "communication": {"type": "object", "additionalProperties": False, "required": [
            "verbosity", "humor", "emotional_expression", "vocabulary_style"], "properties": {
            "verbosity": {"enum": ["low", "medium", "high"]},
            "humor": {"enum": ["none", "light", "moderate", "high"]},
            "emotional_expression": {"enum": ["low", "medium", "high"]},
            "vocabulary_style": {"enum": ["plain", "cute", "formal"]}}},
        "behavior": {"type": "object", "additionalProperties": False, "required": [
            "unsolicited_greetings", "proactive_questions", "tamagotchi_growth",
            "psychological_growth_features"], "properties": {
            "unsolicited_greetings": {"type": "boolean"},
            "proactive_questions": {"enum": ["never", "rare", "occasional", "frequent"]},
            "tamagotchi_growth": {"type": "boolean"},
            "psychological_growth_features": {"enum": ["off", "opt_in", "on"]}}},
    },
}
_VALIDATOR = Draft202012Validator(SCHEMA)

# Sources allowed to change settings. Conversation/LLM text can never rewrite configuration.
USER_SETTINGS = "user_settings"
MAX_TRAIT_DRIFT = 0.15


class ConfigError(Exception):
    pass


class PermissionDenied(ConfigError):
    pass


def validate(cfg: dict) -> dict:
    errs = sorted(_VALIDATOR.iter_errors(cfg), key=lambda e: list(e.path))
    if errs:
        loc = ".".join(str(p) for p in errs[0].path) or "<root>"
        raise ConfigError(f"{loc}: {errs[0].message}")
    return cfg


def load(path: str | Path) -> dict:
    try:
        cfg = yaml.safe_load(Path(path).read_text())
    except (OSError, yaml.YAMLError) as e:
        raise ConfigError(f"cannot read {path}: {e}") from None
    return validate(cfg)


def apply_update(cfg: dict, patch: dict, source: str) -> dict:
    """Return a new validated config, or raise. The input config is never mutated."""
    if source != USER_SETTINGS:
        raise PermissionDenied(f"source {source!r} may not change configuration")
    new = copy.deepcopy(cfg)

    def merge(dst: dict, src: dict, path: str = "") -> None:
        for k, v in src.items():
            if k not in dst:
                raise ConfigError(f"unknown key {path}{k}")
            if isinstance(v, dict) and isinstance(dst[k], dict):
                merge(dst[k], v, f"{path}{k}.")
            else:
                dst[k] = v

    merge(new, patch)
    return validate(new)


class Personality:
    """Stable baseline (from config) plus small bounded drift from experience."""

    def __init__(self, cfg: dict):
        self.cfg = validate(copy.deepcopy(cfg))
        self.baseline = dict(cfg["traits"])
        self.drift = {t: 0.0 for t in _TRAITS}

    def adapt(self, trait: str, delta: float) -> float:
        if trait not in self.drift:
            raise ConfigError(f"unknown trait {trait}")
        self.drift[trait] = max(-MAX_TRAIT_DRIFT, min(MAX_TRAIT_DRIFT, self.drift[trait] + delta))
        return self.value(trait)

    def value(self, trait: str) -> float:
        return max(0.0, min(1.0, self.baseline[trait] + self.drift[trait]))
