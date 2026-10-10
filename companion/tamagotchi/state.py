"""Character state: needs, mood, growth. Pure logic with an injected clock (epoch seconds).

Design guarantees (tested):
- all values bounded; clock going backwards is ignored
- absence never lowers affection, trust, experience or stage; happiness has a floor
- growth needs xp AND variety AND per-kind counts; per-kind diminishing returns and daily caps
  stop grinding one action
- mood describes the creature, never the user (no guilt, no distress about being left alone)
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

MAX_ADVANCE_HOURS = 24 * 14  # cap catch-up so a bad clock jump cannot do wild things
RECENT_MAX = 10
SCHEMA_VERSION = 1
UNIT_FIELDS = ["energy", "sleepiness", "happiness", "affection", "trust", "curiosity", "playfulness"]


def _clamp(x: float) -> float:
    return max(0.0, min(1.0, float(x)))


def _toward(value: float, target: float, step: float) -> float:
    if value < target:
        return min(target, value + step)
    return max(target, value - step)


class CharacterState:
    def __init__(self, rules: dict, now: float, tz: timezone | None = None):
        self.rules = rules
        self.tz = tz or timezone.utc
        n = rules["needs"]
        self.energy, self.sleepiness = 0.9, 0.1
        self.happiness, self.affection, self.trust = n["happiness_baseline"], 0.1, 0.1
        self.curiosity, self.playfulness = n["curiosity_baseline"], n["playfulness_baseline"]
        self.experience = 0.0
        self.stage = rules["stages"][0]["name"]
        self.sleeping = False
        self.current_activity = "idle"
        self.last_update = now
        self.last_interaction: float | None = None
        self.kind_counts: dict[str, int] = {}
        self.recent: list[dict] = []
        self.day = {"key": self._day_key(now), "times": {}, "xp": {}}

    # ---- time -----------------------------------------------------------
    def _day_key(self, now: float) -> str:
        return datetime.fromtimestamp(now, self.tz).strftime("%Y-%m-%d")

    def advance(self, now: float) -> None:
        dt = now - self.last_update
        if dt <= 0:
            return  # backwards or same clock: ignore, never rewind
        hours = min(dt / 3600, MAX_ADVANCE_HOURS)
        n = self.rules["needs"]
        if self.sleeping:
            self.energy = _clamp(self.energy + n["energy_recovery_per_hour"] * hours)
            self.sleepiness = _clamp(self.sleepiness - n["sleepiness_recovery_per_hour"] * hours)
        else:
            self.energy = _clamp(self.energy - n["energy_decay_per_hour"] * hours)
            self.sleepiness = _clamp(self.sleepiness + n["sleepiness_gain_per_hour"] * hours)
        step = n["drift_per_hour"] * hours
        self.happiness = max(n["happiness_floor"], _toward(self.happiness, n["happiness_baseline"], step))
        self.curiosity = _toward(self.curiosity, n["curiosity_baseline"], step)
        self.playfulness = _toward(self.playfulness, n["playfulness_baseline"], step)
        self.last_update = now
        if self._day_key(now) != self.day["key"]:
            self.day = {"key": self._day_key(now), "times": {}, "xp": {}}

    def set_sleeping(self, sleeping: bool, now: float) -> None:
        self.advance(now)
        self.sleeping = sleeping
        self.current_activity = "sleeping" if sleeping else "idle"

    # ---- events ---------------------------------------------------------
    def apply_event(self, kind: str, now: float) -> dict:
        """Returns {"xp_gained": float, "stage_changed": str|None}. Unknown kinds raise KeyError."""
        spec = self.rules["events"][kind]
        self.advance(now)
        times = self.day["times"].get(kind, 0)
        spent = self.day["xp"].get(kind, 0.0)
        gain = spec["xp"] * 0.5 ** (times / spec["half_life"])
        gain = max(0.0, min(gain, spec["daily_xp_cap"] - spent))
        self.day["times"][kind] = times + 1
        self.day["xp"][kind] = spent + gain
        for field in ("happiness", "affection", "trust", "curiosity", "playfulness", "energy"):
            if field in spec:
                # effects also diminish with repetition so spamming one action cannot max a stat
                setattr(self, field, _clamp(getattr(self, field) + spec[field] * 0.5 ** (times / spec["half_life"])))
        self.experience += gain
        self.kind_counts[kind] = self.kind_counts.get(kind, 0) + 1
        self.last_interaction = now
        self.recent = (self.recent + [{"kind": kind, "t": now}])[-RECENT_MAX:]
        self.current_activity = kind
        changed = self._maybe_grow() if self.rules["growth_enabled"] else None
        return {"xp_gained": gain, "stage_changed": changed}

    def _maybe_grow(self) -> str | None:
        names = [s["name"] for s in self.rules["stages"]]
        idx = names.index(self.stage)
        if idx + 1 >= len(names):
            return None
        nxt = self.rules["stages"][idx + 1]
        kinds = sum(1 for c in self.kind_counts.values() if c > 0)
        ok = (self.experience >= nxt["min_xp"] and kinds >= nxt["min_kinds"]
              and all(self.kind_counts.get(k, 0) >= c for k, c in nxt["required"].items()))
        if ok:
            self.stage = nxt["name"]  # at most one stage per event; never regresses
            return self.stage
        return None

    # ---- derived (read-only) -------------------------------------------
    @property
    def mood(self) -> str:
        if self.sleeping:
            return "sleeping"
        if self.energy < 0.2 or self.sleepiness > 0.8:
            return "sleepy"
        if self.playfulness > 0.7 and self.energy > 0.4:
            return "playful"
        if self.curiosity > 0.75:
            return "curious"
        if self.happiness > 0.75:
            return "happy"
        if self.happiness < 0.45:
            return "subdued"
        return "calm"

    @property
    def relationship_state(self) -> str:
        score = (self.trust + self.affection) / 2
        return "close" if score >= 0.6 else "familiar" if score >= 0.3 else "new"

    @property
    def animation_speed(self) -> float:
        """Multiplier hint for the host to pass when choosing animations: sleepier is slower."""
        return round(0.6 + 0.4 * self.energy, 2)

    # ---- persistence ----------------------------------------------------
    def to_dict(self) -> dict:
        d = {f: getattr(self, f) for f in UNIT_FIELDS}
        d.update(schema_version=SCHEMA_VERSION, experience=self.experience, stage=self.stage,
                 sleeping=self.sleeping, current_activity=self.current_activity,
                 last_update=self.last_update, last_interaction=self.last_interaction,
                 kind_counts=self.kind_counts, recent=self.recent, day=self.day)
        return copy.deepcopy(d)

    @classmethod
    def from_dict(cls, d: dict, rules: dict, now: float, tz: timezone | None = None) -> "CharacterState":
        """Strict: raises ValueError on anything out of range or unknown."""
        s = cls(rules, now, tz)
        if d.get("schema_version") != SCHEMA_VERSION:
            raise ValueError("schema_version")
        for f in UNIT_FIELDS:
            v = d[f]
            if not isinstance(v, (int, float)) or isinstance(v, bool) or not 0 <= v <= 1:
                raise ValueError(f)
            setattr(s, f, float(v))
        if not isinstance(d["experience"], (int, float)) or d["experience"] < 0:
            raise ValueError("experience")
        s.experience = float(d["experience"])
        if d["stage"] not in [st["name"] for st in rules["stages"]]:
            raise ValueError("stage")
        s.stage = d["stage"]
        s.sleeping = bool(d["sleeping"])
        s.current_activity = str(d["current_activity"])[:32]
        s.last_update = min(float(d["last_update"]), now) if now else float(d["last_update"])
        s.last_interaction = d["last_interaction"]
        s.kind_counts = {str(k): int(v) for k, v in d["kind_counts"].items() if int(v) >= 0}
        s.recent = list(d["recent"])[-RECENT_MAX:]
        s.day = {"key": str(d["day"]["key"]), "times": dict(d["day"]["times"]), "xp": dict(d["day"]["xp"])}
        return s


class StateStore:
    """Checksummed atomic JSON file. Corrupt or missing file -> fresh state, reported via .recovered."""

    def __init__(self, path: Path, rules: dict):
        self.path, self.rules, self.recovered = Path(path), rules, False

    def save(self, state: CharacterState) -> None:
        body = state.to_dict()
        digest = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"state": body, "sha256": digest}))
        os.replace(tmp, self.path)

    def load(self, now: float, tz: timezone | None = None) -> CharacterState:
        self.recovered = False
        if not self.path.exists():
            return CharacterState(self.rules, now, tz)
        try:
            blob = json.loads(self.path.read_text())
            body = blob["state"]
            if hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest() != blob["sha256"]:
                raise ValueError("checksum")
            return CharacterState.from_dict(body, self.rules, now, tz)
        except (ValueError, KeyError, TypeError, OSError, AttributeError):
            self.recovered = True
            return CharacterState(self.rules, now, tz)
