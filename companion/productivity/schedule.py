"""When is a reminder due? Pure functions on a reminder dict (no clock, no database), so recurrence and daylight-saving behaviour
can be tested by passing any 'now'.

A reminder says WHAT LOCAL TIME on the wall clock of ITS OWN time zone, never a fixed UTC offset, so 3:00 PM stays 3:00 PM across
daylight-saving changes. Rules for the two awkward days a year:
- a wall time that does not exist (spring forward, e.g. 2:30 AM) happens at the same moment 2:30 would have, which is 3:30 AM: once;
- a wall time that happens twice (fall back, e.g. 1:30 AM) is due at its FIRST occurrence only: once, never twice.
Every timestamp is UTC epoch seconds; occurrences are identified by (reminder id, due timestamp), so the same moment can never be
counted twice."""
from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

KINDS = ("once", "daily", "weekly", "interval")
WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
_HM = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
HORIZON_DAYS = 400


class ScheduleError(ValueError):
    pass


def parse_hm(s: str) -> time:
    m = _HM.match(s or "")
    if not m:
        raise ScheduleError("time must look like 15:00")
    return time(int(m.group(1)), int(m.group(2)))


def parse_date(s: str | None) -> date | None:
    if s in (None, ""):
        return None
    if not isinstance(s, str) or not _DATE.match(s):
        raise ScheduleError("date must look like 2026-10-12")
    try:
        return date.fromisoformat(s)
    except ValueError:
        raise ScheduleError("that date does not exist") from None


def validate(rem: dict) -> dict:
    """Raises ScheduleError for a schedule that could not run; returns the same dict."""
    kind = rem.get("kind")
    if kind not in KINDS:
        raise ScheduleError(f"kind must be one of {KINDS}")
    try:
        ZoneInfo(rem.get("tz") or "")
    except Exception:
        raise ScheduleError("unknown time zone") from None
    wd = rem.get("weekdays") or []
    if not isinstance(wd, list) or any(isinstance(d, bool) or not isinstance(d, int) or not 0 <= d <= 6 for d in wd):
        raise ScheduleError("weekdays are numbers 0 (Monday) to 6 (Sunday)")
    start, end = parse_date(rem.get("start_date")), parse_date(rem.get("end_date"))
    if start and end and end < start:
        raise ScheduleError("the end date is before the start date")
    if kind == "once":
        if not parse_date(rem.get("date")):
            raise ScheduleError("a one-time reminder needs a date")
        parse_hm(rem.get("time"))
    elif kind in ("daily", "weekly"):
        parse_hm(rem.get("time"))
        if kind == "weekly" and not wd:
            raise ScheduleError("a weekly reminder needs at least one day")
    else:
        n = rem.get("interval_minutes")
        if isinstance(n, bool) or not isinstance(n, int) or not 5 <= n <= 24 * 60:
            raise ScheduleError("repeat every 5 minutes to 24 hours")
        a, b = parse_hm(rem.get("window_start")), parse_hm(rem.get("window_end"))
        if b < a:
            raise ScheduleError("the window must end after it starts")
    return rem


def _times_of_day(rem: dict) -> list[time]:
    if rem["kind"] != "interval":
        return [parse_hm(rem["time"])]
    a, b, n = parse_hm(rem["window_start"]), parse_hm(rem["window_end"]), rem["interval_minutes"]
    out, cur = [], a.hour * 60 + a.minute
    end = b.hour * 60 + b.minute
    while cur <= end:
        out.append(time(cur // 60, cur % 60))
        cur += n
    return out


def next_due(rem: dict, after: float) -> float | None:
    """The first due moment strictly after `after` (UTC epoch seconds), or None if there is none."""
    tz = ZoneInfo(rem["tz"])
    start, end = parse_date(rem.get("start_date")), parse_date(rem.get("end_date"))
    weekdays = set(rem.get("weekdays") or [])
    times = _times_of_day(rem)
    day0 = datetime.fromtimestamp(after, tz).date() - timedelta(days=1)       # one day back: a wall time can map across midnight
    for i in range(HORIZON_DAYS):
        d = day0 + timedelta(days=i)
        if end and d > end:
            return None
        if start and d < start:
            continue
        if rem["kind"] == "once":
            if d != parse_date(rem["date"]):
                continue
        elif weekdays and d.weekday() not in weekdays:
            continue
        cands = sorted(datetime.combine(d, t, tzinfo=tz).timestamp() for t in times)       # fold=0: first occurrence, gap = shifted forward
        for ts in cands:
            if ts > after:
                return ts
        if rem["kind"] == "once":
            return None
    return None


def upcoming(rem: dict, after: float, count: int = 5, until: float | None = None) -> list[float]:
    out, t = [], after
    while len(out) < count:
        t = next_due(rem, t)
        if t is None or (until is not None and t > until):
            break
        out.append(t)
    return out


def describe(rem: dict) -> str:
    """Plain words for the schedule, in the reminder's own wall clock ('every day at 3:00 PM')."""
    def hm(s):
        t = parse_hm(s)
        return datetime(2000, 1, 1, t.hour, t.minute).strftime("%-I:%M %p")
    k = rem["kind"]
    days = sorted(set(rem.get("weekdays") or []))
    if k == "once":
        d = parse_date(rem["date"])
        return f"{d.strftime('%A, %B %-d')} at {hm(rem['time'])}"
    if k == "daily":
        base = "every day" if not days or len(days) == 7 else _days_phrase(days)
        return f"{base} at {hm(rem['time'])}"
    if k == "weekly":
        return f"{_days_phrase(days)} at {hm(rem['time'])}"
    n = rem["interval_minutes"]
    every = f"{n // 60} hour{'s' if n // 60 != 1 else ''}" if n % 60 == 0 else f"{n} minutes"
    base = f"every {every} from {hm(rem['window_start'])} to {hm(rem['window_end'])}"
    return base if not days or len(days) == 7 else f"{base} on {_days_phrase(days, every=False)}"


def _days_phrase(days: list[int], every: bool = True) -> str:
    if days == [0, 1, 2, 3, 4]:
        return "every weekday" if every else "weekdays"
    if days == [5, 6]:
        return "every weekend" if every else "weekends"
    names = [WEEKDAYS[d] for d in days]
    joined = names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]
    return ("every " if every else "") + joined
