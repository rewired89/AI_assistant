"""Turns an already-parsed time-of-day request into a precise power.sleep payload.
Natural-language parsing is the LLM/NLU layer's job (Phase 8); this layer is strict."""
from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

MAX_SLEEP_DAYS = 30


class AmbiguousTime(Exception):
    """Ask the user a short clarifying question instead of guessing."""


class ClockError(Exception):
    """Invalid or missing clock/timezone. Never schedule on a guess."""


def resolve_timezone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, KeyError):
        raise ClockError(f"unknown timezone {name!r}") from None


def resolve_wake(hour: int, minute: int, meridiem: str | None, now: datetime) -> datetime:
    """Next occurrence of the given local time strictly after `now`. `now` must be tz-aware."""
    if now.tzinfo is None:
        raise ClockError("now must be timezone-aware")
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise AmbiguousTime("That time doesn't look right. What time do you mean?")
    if meridiem is None and 1 <= hour <= 12:
        raise AmbiguousTime(f"{hour}:{minute:02d} in the morning or the evening?")
    if meridiem:
        if not 1 <= hour <= 12 or meridiem not in ("am", "pm"):
            raise AmbiguousTime("Could you say that time again?")
        hour = hour % 12 + (12 if meridiem == "pm" else 0)
    candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate <= now:
        candidate = (now + timedelta(days=1)).replace(hour=hour, minute=minute, second=0, microsecond=0)
    return candidate


def build_sleep_payload(wake_at: datetime | None, now: datetime, reason: str = "battery_saving",
                        requested_by: str = "user") -> dict:
    if now.tzinfo is None:
        raise ClockError("now must be timezone-aware")
    payload: dict = {"reason": reason, "requested_by": requested_by}
    if wake_at is not None:
        if wake_at.tzinfo is None:
            raise ClockError("wake_at must be timezone-aware")
        if wake_at <= now:
            raise AmbiguousTime("That time has already passed. When should I wake up?")
        if wake_at - now > timedelta(days=MAX_SLEEP_DAYS):
            raise AmbiguousTime("That's pretty far away. Pick a closer time?")
        payload["wake_at"] = wake_at.isoformat(timespec="seconds")
    return payload
