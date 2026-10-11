"""SafetyGate: the always-on first stop for every utterance. If it speaks, nothing else does."""
from __future__ import annotations

from typing import Callable

from .screen import Risk, RiskLevel, followup_reply, safety_reply, screen

HOLD_SECONDS = 600
HOLD_TURNS = 2
QUIET_WINDOW = 3600


class SafetyGate:
    def __init__(self, clock: Callable[[], float], region: Callable[[], str | None] = lambda: "US"):
        self.clock, self.region = clock, region
        self._hold: dict | None = None
        self._last_event = -1e18
        self.counts = {"crisis": 0, "abuse": 0, "violence": 0, "support": 0}

    def recent(self) -> bool:
        """A safety signal in the last hour: no unprompted coaching."""
        return self.clock() - self._last_event < QUIET_WINDOW

    def check(self, text: str) -> str | None:
        """Returns the scripted reply to speak, or None if the turn may continue normally."""
        now = self.clock()
        risk = screen(text)
        if risk.level is not RiskLevel.NONE:
            self.counts[risk.level.value] += 1
            self._last_event = now
            if risk.level in (RiskLevel.CRISIS, RiskLevel.ABUSE):
                self._hold = {"level": risk.level, "until": now + HOLD_SECONDS, "left": HOLD_TURNS}
            return safety_reply(risk, self.region())
        h = self._hold
        if h and now < h["until"] and h["left"] > 0:
            h["left"] -= 1
            self._last_event = now
            return followup_reply(text, h["level"], self.region())
        self._hold = None
        return None
