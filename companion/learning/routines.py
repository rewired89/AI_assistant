"""Routine recognition (spec section 11): notice, from enough evidence, things like "she usually chats in the evening" or
"she usually greets me with 'hey'", and ONLY keep them if she says yes.

What is watched: only her conversations with the robot (nothing outside the device's own interactions). What is kept as
evidence: a timestamp, the kind of event, the part of the day, and for greetings one word from a fixed list. Never what she
said. Evidence is pruned after `retention_days`. Nothing is inferred from one day: it takes `min_days` different days inside
`window_days`, and a clear majority, before a routine is even proposed.
A proposed routine is stored as a PENDING memory (an AI inference, never an established fact). It becomes real only when she
confirms by voice. She can list, correct, delete, or turn off routine learning completely; turning it off erases the
evidence. A "no" suppresses that routine for `decline_days`.
Not used for anything yet except being remembered and available to the conversation; it never changes the character's
configuration."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, tzinfo, timezone
from typing import Callable

from ..memory import Disabled, MemoryError_
from ..storage import ChecksumJSON

DAY = 86400.0
GREETINGS = ("hi", "hello", "hey", "hiya", "howdy", "yo", "good morning", "good afternoon", "good evening")
_GREET = re.compile(r"^\W*(hi|hello|hey|hiya|howdy|yo|good (?:morning|afternoon|evening))\b", re.I)
KEY_CHAT = "routine_chat_time"
KEY_GREETING = "routine_greeting"
ROUTINE_KEYS = (KEY_CHAT, KEY_GREETING)


@dataclass
class LearnConfig:
    window_days: int = 21
    min_days: int = 4              # distinct days with the pattern
    min_chat_days: int = 5         # distinct days she talked to the robot at all
    min_share: float = 0.5         # the pattern must cover at least this share of her chat days
    greeting_min_count: int = 4
    greeting_min_days: int = 3
    greeting_min_share: float = 0.6
    decline_days: int = 30
    ask_cooldown_days: int = 3
    retention_days: int = 45
    max_observations: int = 2000


def part_of_day(hour: int) -> str:
    return "morning" if 5 <= hour < 11 else "afternoon" if 11 <= hour < 17 else "evening" if 17 <= hour < 22 else "night"


class RoutineLearner:
    def __init__(self, path, clock: Callable[[], float], tz: tzinfo = timezone.utc, memory=None,
                 config: LearnConfig | None = None):
        self.clock, self.tz, self.memory, self.cfg = clock, tz, memory, config or LearnConfig()
        self.store = ChecksumJSON(path)
        self.d = self.store.load({"v": 1, "enabled": True, "obs": [], "asked": {}, "declined": {}})
        self.recovered = self.store.recovered

    # ---- switches -------------------------------------------------------------------------------
    @property
    def enabled(self) -> bool:
        if not self.d["enabled"]:
            return False
        if self.memory is not None:
            try:
                return bool(self.memory.settings()["goal_habit"]["enabled"])      # she turned that memory category off
            except (MemoryError_, KeyError):
                return False
        return True

    def set_enabled(self, on: bool) -> None:
        self.d["enabled"] = bool(on)
        if not on:
            self.d["obs"] = []                                                    # turning it off erases the evidence
        self._save()

    def forget_evidence(self) -> int:
        n = len(self.d["obs"])
        self.d["obs"], self.d["asked"] = [], {}
        self._save()
        return n

    def _save(self) -> None:
        self.store.save(self.d)

    # ---- evidence ---------------------------------------------------------------------------------
    def _local(self, ts: float) -> datetime:
        return datetime.fromtimestamp(ts, self.tz)

    def _add(self, kind: str, detail: str, now: float) -> None:
        obs = self.d["obs"]
        obs.append([now, kind, detail])
        cutoff = now - self.cfg.retention_days * DAY
        if len(obs) > self.cfg.max_observations or obs[0][0] < cutoff:
            self.d["obs"] = [o for o in obs if o[0] >= cutoff][-self.cfg.max_observations:]
        self._save()

    def record_chat(self, utterance: str | None = None, now: float | None = None) -> None:
        """Call once per ordinary conversation turn. Keeps the time of day, and a greeting word if it opens with one."""
        if not self.enabled:
            return
        now = self.clock() if now is None else now
        self._add("chat", part_of_day(self._local(now).hour), now)
        m = _GREET.match(utterance or "")
        if m:
            self._add("greeting", m.group(1).lower(), now)

    # ---- detection --------------------------------------------------------------------------------
    def candidates(self, now: float | None = None) -> list[dict]:
        now = self.clock() if now is None else now
        c, since = self.cfg, now - self.cfg.window_days * DAY
        obs = [o for o in self.d["obs"] if o[0] >= since]
        out = []
        chat = [o for o in obs if o[1] == "chat"]
        days_all = {self._local(o[0]).date() for o in chat}
        if len(days_all) >= c.min_chat_days:
            per = {}
            for o in chat:
                per.setdefault(o[2], set()).add(self._local(o[0]).date())
            best = max(per, key=lambda b: len(per[b]))
            n = len(per[best])
            if n >= c.min_days and n / len(days_all) >= c.min_share:
                out.append({"key": KEY_CHAT, "value": f"She usually likes to chat in the {best}.", "days": n,
                            "question": f"I've noticed you often talk with me in the {best}. Do you want me to remember that?"})
        greet = [o for o in obs if o[1] == "greeting"]
        if len(greet) >= c.greeting_min_count:
            words = {}
            for o in greet:
                words.setdefault(o[2], []).append(self._local(o[0]).date())
            w = max(words, key=lambda k: len(words[k]))
            days = len(set(words[w]))
            if len(words[w]) >= c.greeting_min_count and days >= c.greeting_min_days and len(words[w]) / len(greet) >= c.greeting_min_share:
                out.append({"key": KEY_GREETING, "value": f"She usually says '{w}' when she greets me.", "days": days,
                            "question": f"You often start with '{w}'. Do you want me to remember that you like to greet me that way?"})
        return out

    def _suppressed(self, key: str, now: float) -> bool:
        return now < self.d["declined"].get(key, 0.0)

    def propose(self, now: float | None = None) -> list[str]:
        """Stores newly found routines as PENDING memories. Returns their ids. Never overwrites something she confirmed."""
        if not self.enabled or self.memory is None:
            return []
        now = self.clock() if now is None else now
        ids = []
        for cand in self.candidates(now):
            if self._suppressed(cand["key"], now):
                continue
            try:
                ids.append(self.memory.add({"category": "goal_habit", "key": cand["key"], "value": cand["value"],
                                            "source": "ai_inference", "confidence": min(1.0, cand["days"] / 10),
                                            "source_context": f"seen on {cand['days']} different days"}))
            except (Disabled, MemoryError_):
                continue
        return ids

    # ---- asking her -------------------------------------------------------------------------------
    def next_question(self, now: float | None = None) -> dict | None:
        """A pending routine worth asking about now ({'id','key','question'}), at most one per cooldown per routine."""
        if not self.enabled or self.memory is None:
            return None
        now = self.clock() if now is None else now
        self.propose(now)
        questions = {c["key"]: c["question"] for c in self.candidates(now)}
        for rec in self.memory.list("goal_habit"):
            key = rec.get("key")
            if key in ROUTINE_KEYS and rec["status"] == "pending" and key in questions:
                if now - self.d["asked"].get(key, 0.0) < self.cfg.ask_cooldown_days * DAY or self._suppressed(key, now):
                    continue
                self.d["asked"][key] = now
                self._save()
                return {"id": rec["id"], "key": key, "question": questions[key]}
        return None

    def decline(self, key: str, now: float | None = None) -> None:
        now = self.clock() if now is None else now
        self.d["declined"][key] = now + self.cfg.decline_days * DAY
        self._save()

    # ---- what she can inspect ----------------------------------------------------------------------
    def routines(self) -> list[dict]:
        if self.memory is None:
            return []
        return [r for r in self.memory.list("goal_habit") if r.get("key") in ROUTINE_KEYS]

    def forget_routines(self) -> int:
        n = 0
        for r in self.routines():
            try:
                self.memory.delete(r["id"])
                n += 1
            except MemoryError_:
                pass
        self.forget_evidence()
        return n
