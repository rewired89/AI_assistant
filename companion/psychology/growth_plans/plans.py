"""Growth goals she chooses, kept in the private vault only with her consent.

Tracks what she chose and what she says she did: no score, no streak, no maturity rating, nothing computed about
her. Goals are hers; the robot may offer examples but never picks one for her."""
from __future__ import annotations

import uuid
from typing import Callable

from ...memory.store import ConsentRequired, ReflectionVault

MAX_ACTIVE = 3
MAX_TITLE = 100
OUTCOMES = ("did it", "partly", "not yet", "changed my mind")
SUGGESTIONS = [
    "say what I need instead of expecting someone to guess",
    "ask for a break before I'm overwhelmed",
    "agree on a time to come back to an important conversation",
    "listen to the other person's view before I answer",
    "hear that something I did had an impact without feeling condemned",
    "follow through on an agreement",
    "disagree without attacking",
]


class GoalError(Exception):
    pass


class GoalStore:
    def __init__(self, vault: ReflectionVault, clock: Callable[[], float]):
        self.vault, self.clock = vault, clock

    def _goals(self) -> list[dict]:
        return [r for r in self.vault.list("reflection") if "growth_goal" in r["tags"]]

    def active(self) -> list[dict]:
        return [g for g in self._goals() if "active" in g["tags"]]

    def add_goal(self, title: str, consent: bool) -> str:
        title = " ".join(title.split())
        if not consent:
            raise ConsentRequired("ask before keeping notes about a goal")
        if not 3 <= len(title) <= MAX_TITLE:
            raise GoalError(f"goal must be 3-{MAX_TITLE} characters")
        if len(self.active()) >= MAX_ACTIVE:
            raise GoalError(f"at most {MAX_ACTIVE} active goals; finish or drop one first")
        gid = "goal_" + uuid.uuid4().hex[:8]
        self.vault.add({"category": "reflection", "key": gid, "value": title, "tags": ["growth_goal", "active"],
                        "source": "explicit_user_statement"}, consent=True)
        return gid

    def _find(self, gid: str) -> dict:
        for g in self._goals():
            if g["key"] == gid:
                return g
        raise GoalError("no such goal")

    def check_in(self, gid: str, outcome: str, note: str, consent: bool) -> None:
        if outcome not in OUTCOMES:
            raise GoalError("outcome must be one of " + ", ".join(OUTCOMES))
        if "active" not in self._find(gid)["tags"]:
            raise GoalError("goal is not active")
        n = sum(1 for r in self.vault.list("reflection") if r["key"] and r["key"].startswith(f"ci_{gid}_")) + 1
        self.vault.add({"category": "reflection", "key": f"ci_{gid}_{n}", "value": f"{outcome}: {' '.join(note.split())[:400]}".strip(": "),
                        "tags": ["checkin", gid], "source": "explicit_user_statement"}, consent=consent)

    def set_status(self, gid: str, status: str) -> None:
        if status not in ("active", "done", "dropped", "changed"):
            raise GoalError("bad status")
        g = self._find(gid)
        tags = [t for t in g["tags"] if t not in ("active", "done", "dropped", "changed")] + [status]
        self.vault.update(g["id"], {"tags": tags})

    def history(self, gid: str) -> list[dict]:
        return [r for r in self.vault.list("reflection") if gid in r["tags"] and "checkin" in r["tags"]]

    def delete_goal(self, gid: str) -> int:
        g = self._find(gid)
        n = 0
        for r in self.vault.list("reflection"):
            if r["id"] == g["id"] or (gid in r["tags"] and "checkin" in r["tags"]):
                self.vault.delete(r["id"])
                n += 1
        return n
