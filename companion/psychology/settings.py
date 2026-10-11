"""Psychology feature settings. Content-free on purpose: consent, pauses and reminder times only, so this file
reveals nothing about what she talked about. (Notes live in the private vault.)"""
from __future__ import annotations

import uuid
from pathlib import Path
from typing import Callable

from ..storage import ChecksumJSON

DISCLOSURE_VERSION = 1
MAX_FOLLOWUPS = 5


class PsychologySettings:
    def __init__(self, path: Path | str, clock: Callable[[], float]):
        self.store, self.clock = ChecksumJSON(path), clock
        self.d = self.store.load({"enabled": True, "ack_version": 0, "paused_until": 0.0, "declined_until": 0.0,
                                  "region": "US", "followups": [], "last_offer_day": ""})
        self.recovered = self.store.recovered

    def save(self) -> None:
        self.store.save(self.d)

    # ---- consent / switches --------------------------------------------------------------------
    @property
    def acknowledged(self) -> bool:
        return self.d["ack_version"] >= DISCLOSURE_VERSION

    def acknowledge(self) -> None:
        self.d["ack_version"] = DISCLOSURE_VERSION
        self.save()

    def revoke_ack(self) -> None:
        self.d["ack_version"] = 0
        self.save()

    @property
    def active(self) -> bool:
        return bool(self.d["enabled"]) and self.clock() >= self.d["paused_until"]

    def set_enabled(self, on: bool) -> None:
        self.d["enabled"] = bool(on)
        self.save()

    def pause(self, seconds: float) -> None:
        self.d["paused_until"] = self.clock() + seconds
        self.save()

    def decline(self, days: float = 3.0) -> None:
        self.d["declined_until"] = self.clock() + days * 86400
        self.save()

    @property
    def region(self) -> str:
        return self.d["region"]

    def set_region(self, region: str) -> None:
        self.d["region"] = region.upper()[:8]
        self.save()

    # ---- content-free reminders -------------------------------------------------------------------
    def add_followup(self, due_at: float, label: str = "a plan you made") -> str | None:
        if len(self.d["followups"]) >= MAX_FOLLOWUPS or due_at <= self.clock():
            return None
        fid = uuid.uuid4().hex[:8]
        self.d["followups"].append({"id": fid, "due_at": due_at, "label": label[:40], "announced": False})
        self.save()
        return fid

    def due_followups(self) -> list[dict]:
        now = self.clock()
        return [f for f in self.d["followups"] if f["due_at"] <= now and not f["announced"]]

    def mark_announced(self, fid: str) -> None:
        for f in self.d["followups"]:
            if f["id"] == fid:
                f["announced"] = True
        self.save()

    def remove_followup(self, fid: str) -> None:
        self.d["followups"] = [f for f in self.d["followups"] if f["id"] != fid]
        self.save()

    def clear_followups(self) -> None:
        self.d["followups"] = []
        self.save()

    def purge_old(self, max_age_s: float = 3 * 86400) -> None:
        self.d["followups"] = [f for f in self.d["followups"] if self.clock() - f["due_at"] < max_age_s]
        self.save()
