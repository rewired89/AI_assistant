"""Slow, bounded, visible change in how the character acts, driven only by WHAT SHE DOES WITH IT (counts of activities),
never by what she says and never by a language model.

Rules (all enforced here and tested):
- Input is only the tamagotchi's per-kind activity counters (play, teach, ...). No text, no transcripts, no memories.
- Each activity nudges one trait up a tiny amount; a trait can gain at most DAILY_CAP per day and never exceeds MAX_DRIFT
  above its baseline. Drift is never negative: nothing makes her colder or more anxious, and absence changes nothing bad.
- Unused drift fades back toward the baseline (FADE_PER_DAY), so it always reverts on its own.
- It never edits the config or the prompt. The behaviour engine reads `Personality.value()` (baseline + drift); that is all.
- She can ask what changed, switch it off, and reset it, by voice (see learning/memory_talk.py). Off and reset erase the drift.
- State is one small JSON file in the data folder; a damaged file is treated as no drift."""
from __future__ import annotations

import json
import os
import time
from datetime import datetime
from pathlib import Path

from .config import MAX_TRAIT_DRIFT, Personality

SCHEMA_VERSION = 1
MAX_DRIFT = min(0.10, MAX_TRAIT_DRIFT)         # tighter than the personality's own hard limit
NUDGE = 0.004                                    # per counted activity
DAILY_CAP = 0.012                                # per trait per local day
FADE_PER_DAY = 0.002                             # unused drift returns toward baseline
# activity kind -> trait it gently strengthens (kinds come from species/*/growth.yaml)
TRAIT_FOR_KIND = {"play": "playful", "teach": "curious", "explore_topic": "curious",
                  "greeting": "affectionate", "conversation": "affectionate", "feed": "affectionate"}
WORDS = {"playful": "more playful", "curious": "more curious", "affectionate": "more affectionate"}


class PersonalityAdapter:
    def __init__(self, path: str | Path, personality: Personality, clock=time.time, tz=None, enabled: bool = True):
        self.path, self.p, self.clock, self.tz = Path(path), personality, clock, tz
        self.enabled = enabled
        self._seen: dict[str, int] | None = None
        self._day: str | None = None
        self._used: dict[str, float] = {}
        self._fade_day: str | None = None
        self._load()

    # ---- state -----------------------------------------------------------------------------------
    def _today(self) -> str:
        return datetime.fromtimestamp(self.clock(), self.tz).strftime("%Y-%m-%d")

    def _load(self) -> None:
        try:
            d = json.loads(self.path.read_text())
            if d.get("schema_version") != SCHEMA_VERSION:
                raise ValueError
            for t, v in d["drift"].items():
                if t in self.p.drift and isinstance(v, (int, float)):
                    self.p.drift[t] = max(0.0, min(MAX_DRIFT, float(v)))
            self._seen = {str(k): int(v) for k, v in d["seen"].items() if int(v) >= 0}
            self._day, self._fade_day = d.get("day"), d.get("fade_day")
            self._used = {t: float(v) for t, v in d.get("used", {}).items() if t in self.p.drift}
            self.enabled = bool(d.get("enabled", self.enabled)) and self.enabled
        except FileNotFoundError:
            pass
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            self.p.drift = {t: 0.0 for t in self.p.drift}        # damaged file: start from the baseline

    def _save(self) -> None:
        d = {"schema_version": SCHEMA_VERSION, "enabled": self.enabled, "drift": self.p.drift, "seen": self._seen or {},
             "day": self._day, "fade_day": self._fade_day, "used": self._used}
        tmp = self.path.with_suffix(".tmp")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(d, f)
        os.replace(tmp, self.path)

    # ---- the one input ---------------------------------------------------------------------------
    def observe(self, kind_counts: dict[str, int]) -> None:
        """Call regularly with the tamagotchi's lifetime per-kind counters. Only increases since the last call count."""
        counts = {str(k): int(v) for k, v in kind_counts.items()}
        if self._seen is None:
            self._seen = dict(counts)                            # first sight: history before adaptation counts for nothing
            self._save()
            return
        today, changed = self._today(), False
        if today != self._day:
            self._day, self._used, changed = today, {}, True
        if self.enabled:
            if self._fade_day != today:
                if self._fade_day is not None:
                    days = max(1, (datetime.fromisoformat(today) - datetime.fromisoformat(self._fade_day)).days)
                    for t in self.p.drift:
                        self.p.drift[t] = max(0.0, self.p.drift[t] - FADE_PER_DAY * days)
                self._fade_day, changed = today, True
            for kind, n in counts.items():
                trait = TRAIT_FOR_KIND.get(kind)
                new = n - self._seen.get(kind, 0)
                if trait is None or new <= 0:
                    continue
                room = min(DAILY_CAP - self._used.get(trait, 0.0), MAX_DRIFT - self.p.drift[trait])
                gain = max(0.0, min(NUDGE * new, room))
                if gain > 0:
                    self.p.drift[trait] += gain
                    self._used[trait] = self._used.get(trait, 0.0) + gain
                    changed = True
        if counts != self._seen:
            self._seen, changed = dict(counts), True
        if changed:
            self._save()

    # ---- hers to control -------------------------------------------------------------------------
    def reset(self) -> None:
        for t in self.p.drift:
            self.p.drift[t] = 0.0
        self._used = {}
        self._save()

    def set_enabled(self, on: bool) -> None:
        self.enabled = bool(on)
        if not on:
            self.reset()
        else:
            self._save()

    def describe(self) -> str:
        grown = [WORDS[t] for t, v in self.p.drift.items() if v >= 0.02 and t in WORDS]
        if not self.enabled:
            return "I'm not changing at the moment. I stay exactly as I started."
        if not grown:
            return "I haven't changed much yet. I'm still just how I started."
        return "From spending time with you, I've become a little " + " and a little ".join(grown) + "."
