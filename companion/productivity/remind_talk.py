"""Voice commands for reminders. Scripted: no language model reads or writes any of this.

    "remind me to buy batteries tomorrow at 10:30"          one-time
    "remind me to drink water every day at 3 pm"            daily
    "every Monday, Wednesday and Friday at 6 pm remind me to exercise"
    "remind me to stretch every two hours between 9 and 5"
    "remind me in 20 minutes to check the oven"
    "what are my reminders"  /  "pause my water reminder"  /  "delete the exercise reminder" (asks first)
    right after a reminder was spoken: "snooze", "snooze for 15 minutes", "okay" / "thanks", "skip it"

It never guesses a time of day: "at 5" with no am or pm gets a question. It says the reminder is set only after the store accepted it,
and it repeats the schedule back in plain words (including "tomorrow" when the time today has already passed)."""
from __future__ import annotations

import re
import time as _time
from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .reminders import ReminderStore
from .schedule import describe
from .store import StoreError

_I = re.I
CONTEXT_S = 10 * 60.0
PENDING_S = 120.0
DAYS = {"monday": 0, "mon": 0, "tuesday": 1, "tue": 1, "tues": 1, "wednesday": 2, "wed": 2, "thursday": 3, "thu": 3, "thur": 3, "thurs": 3,
        "friday": 4, "fri": 4, "saturday": 5, "sat": 5, "sunday": 6, "sun": 6}
NUMW = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "eight": 8, "ten": 10, "twelve": 12, "fifteen": 15,
        "twenty": 20, "thirty": 30, "forty": 40, "hour": 60}
YES = {"yes", "yeah", "yep", "sure", "ok", "okay", "please", "yup", "confirm", "do"}
NO = {"no", "nope", "don't", "dont", "not", "cancel", "stop", "never", "wait", "nah"}
ORD = {"first": 0, "one": 0, "second": 1, "two": 1, "third": 2, "three": 2, "fourth": 3, "last": -1}

DAYNAME = r"(?:mon|tues?|wed(?:nes)?|thu(?:rs?)?|fri|sat(?:ur)?|sun)(?:day)?s?"
DAYLIST = rf"(?:{DAYNAME}|weekdays?|weekends?)(?:\s*(?:,|and|&)?\s*(?:and\s+)?(?:{DAYNAME}))*"
PLURALDAYS = r"(?:(?:mon|tues|wednes|thurs|fri|satur|sun)days|weekdays|weekends)(?:\s*(?:,|and|&)?\s*(?:and\s+)?(?:(?:mon|tues|wednes|thurs|fri|satur|sun)days))*"
INTERVAL = re.compile(rf"\bevery (?P<n>\d+|a|an|one|two|three|four|five|six|eight|ten|twelve|fifteen|twenty|thirty|forty)? ?(?P<u>hours?|minutes?|mins?)"
                      rf"(?:\s+(?:between|from)\s+(?P<a>[\w:. ]+?)\s+(?:and|to|until)\s+(?P<b>[\w:. ]+?))?(?=\s*(?:[,.]|$|remind| to |on ))", _I)
EVERYDAYS = re.compile(rf"\bevery\s+(?P<d>{DAYLIST})\b|\bon\s+(?P<d2>{PLURALDAYS})\b(?=\s|$|,)", _I)
DAILY = re.compile(r"\b(?:every ?day|daily|each day|every (?P<p>morning|afternoon|evening|night))\b", _I)
RELATIVE = re.compile(r"\bin (?P<n>\d+|a|an|one|two|three|five|ten|fifteen|twenty|thirty|forty|forty-five) (?P<u>minutes?|mins?|hours?)\b", _I)
DATEW = re.compile(rf"\b(?P<w>tomorrow|today|tonight|this (?:morning|afternoon|evening)|(?:on |next )?(?P<dn>{DAYNAME})\b)", _I)
TIME = re.compile(r"(?:\b(?:at|around|by|for)\s+(?P<h>\d{1,2})(?::(?P<m>\d{2}))?\s*(?P<ap>a\.?m\.?|p\.?m\.?|o'?clock)?(?=\W|$))"
                  r"|(?:\b(?P<h2>\d{1,2})(?::(?P<m2>\d{2}))?\s*(?P<ap2>a\.?m\.?|p\.?m\.?)(?=\W|$))"
                  r"|(?:\b(?P<h3>[01]?\d|2[0-3]):(?P<m3>[0-5]\d)\b)|\b(?P<w4>noon|midday|midnight)\b", _I)
PART = re.compile(r"\b(?:in the )?(?P<p>morning|afternoon|evening)\b|\bat (?P<p2>night)\b|\btonight\b", _I)
LEAD = re.compile(r"^\W*(?:(?:hey |ok |okay )?nine ?tails[,.!]?\s*)?(?:please )?(?:can you |could you )?remind me\b[\s,:]*(?:(?:to|about|that) )?", _I)
REMIND_ANY = re.compile(r"\bremind me\b", _I)
LIST = re.compile(r"\bwhat (?:are|were) my reminders\b|\bwhat reminders do i have\b|\bwhat(?:'s| is) (?:coming up|on my schedule)\b|\b(?:read|list|show) (?:me )?my reminders\b|\bany reminders\b", _I)
PAUSE = re.compile(r"\b(?:pause|stop|turn off|disable|mute) (?:my |the )?(?P<t>.+?) reminders?\b", _I)
RESUME = re.compile(r"\b(?:resume|turn on|enable|unpause|restart) (?:my |the )?(?P<t>.+?) reminders?\b", _I)
DELETE = re.compile(r"\b(?:delete|remove|cancel) (?:my |the )?(?P<t>.+?) reminders?\b", _I)
SNOOZE = re.compile(r"^\W*(?:please )?(?:snooze|remind me again|ask me again|later|not now|give me)(?: (?:it|that))?(?:\s+(?:for|in))?\s*(?P<n>\d+|five|ten|fifteen|twenty|thirty|an hour|a few)?\s*(?P<u>minutes?|mins?|hours?)?\W*$", _I)
DISMISS = re.compile(r"^\W*(?:ok(?:ay)?|got it|thanks?|thank you|dismiss(?: it| that)?|done|i did it|i will|alright|sure|will do)\W*(?:nine ?tails)?\W*$", _I)
SKIP = re.compile(r"^\W*(?:skip(?: it| that| this| the next one| tomorrow'?s)?|don'?t remind me (?:again )?(?:tomorrow|next time))\W*$", _I)


@dataclass
class Reply:
    text: str
    style: str = "calm"


def _yes_no(text: str) -> bool | None:
    w = set(re.findall(r"[a-z']+", text.lower()))
    y, n = bool(w & YES), bool(w & NO)
    return True if y and not n else False if n and not y else None


def _num(s: str | None, default: int) -> int:
    if not s:
        return default
    s = s.lower().strip()
    return int(s) if s.isdigit() else NUMW.get(s, default)


class When:
    """What the words said about timing. `clock` is None while the time of day is still unclear (am or pm)."""

    def __init__(self):
        self.kind = None            # once | daily | weekly | interval
        self.weekdays: list[int] = []
        self.date = None            # datetime.date | None
        self.hm: tuple[int, int] | None = None
        self.need_meridiem: tuple[int, int] | None = None
        self.interval = None
        self.window = (9 * 60, 21 * 60)
        self.relative_minutes: int | None = None
        self.hint = None            # morning | afternoon | evening | night
        self.word_date = None


def _hm_from(h: int, m: int, ap: str | None, hint: str | None):
    ap = (ap or "").lower().replace(".", "")
    if ap.startswith("a") or ap.startswith("p"):
        if not 1 <= h <= 12:
            return None, None
        return ((h % 12) + (12 if ap.startswith("p") else 0), m), None
    if h == 0 or h >= 13:
        return (h, m) if h <= 23 else (None, None), None
    if ap.startswith("o") or ap == "":
        if hint in ("morning",):
            return (h % 12, m), None
        if hint in ("afternoon", "evening", "night"):
            return ((h % 12) + 12, m), None
        return None, (h, m)
    return None, (h, m)


def parse_when(text: str, now: datetime) -> tuple[When, str]:
    """Returns (what the words said about timing, the text left over once the timing words are removed)."""
    w, rest = When(), text
    hint = None
    pm = PART.search(rest)
    if pm:
        hint = (pm.group("p") or pm.group("p2") or "night").lower()
        w.hint = hint
    m = INTERVAL.search(rest)
    if m:
        n = _num(m.group("n"), 1)
        unit = m.group("u").lower()
        w.kind, w.interval = "interval", n * (60 if unit.startswith("hour") else 1)
        if m.group("a") and m.group("b"):
            def clock(s):
                mm = re.search(r"(\d{1,2})(?::(\d{2}))?\s*(a\.?m\.?|p\.?m\.?)?", s, _I)
                if not mm:
                    return None
                h, mi = int(mm.group(1)), int(mm.group(2) or 0)
                ap = (mm.group(3) or "").lower().replace(".", "")
                if ap.startswith("p") and h < 12:
                    h += 12
                if ap.startswith("a") and h == 12:
                    h = 0
                if not ap and 1 <= h <= 6:
                    h += 12                                                # "between 9 and 5": 5 means 5 PM
                return h * 60 + mi
            a, b = clock(m.group("a")), clock(m.group("b"))
            if a is not None and b is not None and b > a:
                w.window = (a, b)
        rest = rest[:m.start()] + " " + rest[m.end():]
    m = EVERYDAYS.search(rest)
    if m:
        for tok in re.findall(r"[a-z]+", (m.group("d") or m.group("d2")).lower()):
            if tok.startswith("weekday"):
                w.weekdays += [0, 1, 2, 3, 4]
            elif tok.startswith("weekend"):
                w.weekdays += [5, 6]
            else:
                key = tok.rstrip("s") if tok.rstrip("s") in DAYS else tok
                if key in DAYS:
                    w.weekdays.append(DAYS[key])
        w.weekdays = sorted(set(w.weekdays))
        w.kind = w.kind or "weekly"
        rest = rest[:m.start()] + " " + rest[m.end():]
    m = DAILY.search(rest)
    if m:
        if m.group("p"):
            hint = hint or m.group("p").lower()
            w.hint = hint
        w.kind = w.kind or "daily"
        rest = rest[:m.start()] + " " + rest[m.end():]
    m = RELATIVE.search(rest)
    if m:
        n, u = _num(m.group("n"), 1), m.group("u").lower()
        w.relative_minutes = n * (60 if u.startswith("hour") else 1)
        w.kind = w.kind or "once"
        rest = rest[:m.start()] + " " + rest[m.end():]
    m = DATEW.search(rest)
    if m and not w.relative_minutes:
        word = m.group("w").lower()
        today = now.date()
        if word.startswith("tomorrow"):
            w.date = today + timedelta(days=1)
        elif word in ("today", "tonight") or word.startswith("this "):
            w.date = today
            if word == "tonight":
                hint = hint or "night"
                w.hint = hint
        elif m.group("dn"):
            key = m.group("dn").lower().rstrip("s")
            key = key if key in DAYS else m.group("dn").lower()
            if key in DAYS:
                delta = (DAYS[key] - today.weekday()) % 7
                if delta == 0 or word.startswith("next"):
                    delta = delta or 7
                w.date = today + timedelta(days=delta)
        if w.date:
            w.kind = w.kind or "once"
            rest = rest[:m.start()] + " " + rest[m.end():]
    m = TIME.search(rest)
    if m:
        if m.group("w4"):
            w.hm = (12, 0) if m.group("w4").lower() in ("noon", "midday") else (0, 0)
        else:
            if m.group("h3") is not None:                                                      # 15:30 style
                h, mi, ap = int(m.group("h3")), int(m.group("m3")), ""
            else:
                h, mi = int(m.group("h") or m.group("h2")), int(m.group("m") or m.group("m2") or 0)
                ap = (m.group("ap") or m.group("ap2") or "").lower().replace(".", "")
            w.hm, w.need_meridiem = _hm_from(h, mi, ap, hint)
        rest = rest[:m.start()] + " " + rest[m.end():]
    rest = PART.sub(" ", rest)
    return w, rest


def clean_title(rest: str) -> str:
    t = LEAD.sub("", rest.strip())
    t = re.sub(r"\b(?:remind me|please|nine ?tails)\b", " ", t, flags=_I)
    t = re.sub(r"^\W*(?:to|about|that|and)\b\s*", "", t.strip(), flags=_I)
    t = re.sub(r"\s+(?:to|on|at|and|every|for|by|around|in the)\s*$", "", t.strip(), flags=_I)
    t = re.sub(r"\s+", " ", t).strip(" ,.;:-")
    return (t[:1].upper() + t[1:])[:80] if t else ""


class RemindTalk:
    def __init__(self, reminders: ReminderStore, clock=_time.time, tz: str = "UTC", name=lambda: None, phone_hint: bool = False):
        self.rs, self.clock, self.tz, self.name, self.phone_hint = reminders, clock, tz, name, phone_hint
        self._pend: dict | None = None
        self._ctx: dict | None = None

    def cancel(self) -> None:
        self._pend = None

    def note_spoken(self, occurrence_id: str, now: float | None = None) -> None:
        """The director calls this when a reminder was just spoken, so "snooze" and "okay" mean something for a few minutes."""
        self._ctx = {"id": occurrence_id, "t": self.clock() if now is None else now}

    def _now(self) -> datetime:
        return datetime.fromtimestamp(self.clock(), ZoneInfo(self.tz))

    def _phone(self) -> str:
        return " You can see it on your phone too." if self.phone_hint else ""

    # ---- entry point ---------------------------------------------------------------------------------
    def handle(self, utterance: str) -> Reply | None:
        now = self.clock()
        t = utterance.strip()
        if not t:
            return None
        if self._pend and now - self._pend["t"] > PENDING_S:
            self._pend = None
        pend, self._pend = self._pend, None
        if pend:
            r = self._answer(pend, t)
            if r is not None:
                return r
        ctx = self._ctx if self._ctx and now - self._ctx["t"] <= CONTEXT_S else None
        if ctx:
            r = self._context(ctx, t)
            if r is not None:
                return r
        if REMIND_ANY.search(t):
            return self._create(t)
        if LIST.search(t):
            return self._list()
        for pat, fn in ((DELETE, self._ask_delete), (PAUSE, lambda x: self._toggle(x, False)), (RESUME, lambda x: self._toggle(x, True))):
            m = pat.search(t)
            if m and self.rs.find(m.group("t")):
                return fn(m.group("t"))
            if m and pat is DELETE and re.search(r"\b(?:my|the) .+ reminder", t, _I):
                return Reply(f"I couldn't find a reminder called {m.group('t').strip()}.")
        return None

    # ---- creating ------------------------------------------------------------------------------------
    def _create(self, t: str) -> Reply:
        now = self._now()
        w, rest = parse_when(t, now)
        title = clean_title(rest)
        if not title:
            self._pend = {"t": self.clock(), "action": "ask_title", "when_text": t}
            return Reply("What should I remind you about?")
        return self._finish(title, w, now)

    def _finish(self, title: str, w: When, now: datetime) -> Reply:
        if w.relative_minutes:
            ts = (int(self.clock() // 60) + 1) * 60 + (w.relative_minutes - 1) * 60            # round up to the next whole minute
            dt = datetime.fromtimestamp(ts, ZoneInfo(self.tz))
            return self._save(title, {"kind": "once", "date": dt.strftime("%Y-%m-%d"), "time": dt.strftime("%H:%M")})
        if w.kind is None and w.hm is None and w.need_meridiem is None:
            self._pend = {"t": self.clock(), "action": "ask_when", "title": title}
            return Reply("When should I remind you?")
        if w.need_meridiem:
            self._pend = {"t": self.clock(), "action": "ask_meridiem", "title": title, "w": w}
            h, m = w.need_meridiem
            return Reply(f"Do you mean {h}{':%02d' % m if m else ''} in the morning or in the evening?")
        if w.kind == "interval":
            a, b = w.window
            f = {"kind": "interval", "interval_minutes": w.interval, "window_start": f"{a // 60:02d}:{a % 60:02d}", "window_end": f"{b // 60:02d}:{b % 60:02d}", "weekdays": w.weekdays}
            return self._save(title, f)
        if w.hm is None:
            self._pend = {"t": self.clock(), "action": "ask_time", "title": title, "w": w}
            return Reply("What time?" if not w.date else f"What time {self._day_word(w.date, now)}?")
        hm = f"{w.hm[0]:02d}:{w.hm[1]:02d}"
        if w.kind == "weekly":
            return self._save(title, {"kind": "weekly", "time": hm, "weekdays": w.weekdays})
        if w.kind == "daily":
            return self._save(title, {"kind": "daily", "time": hm, "weekdays": []})
        date = w.date
        said = ""
        if date is None:                                                                      # a time with no day: today if it is still ahead, else tomorrow
            today_at = now.replace(hour=w.hm[0], minute=w.hm[1], second=0, microsecond=0)
            date = now.date() if today_at > now else now.date() + timedelta(days=1)
        return self._save(title, {"kind": "once", "date": date.strftime("%Y-%m-%d"), "time": hm})

    @staticmethod
    def _day_word(d, now: datetime) -> str:
        if d == now.date():
            return "today"
        if d == now.date() + timedelta(days=1):
            return "tomorrow"
        return "on " + d.strftime("%A")

    def _save(self, title: str, f: dict) -> Reply:
        try:
            r = self.rs.create(title=title, tz=self.tz, **f)
        except StoreError as e:
            return Reply(f"I couldn't set that reminder, so nothing was saved. {str(e)[:100]}.")
        except Exception:
            return Reply("I couldn't set that reminder, so nothing was saved.")
        when = describe(r)
        if r["kind"] == "once":
            d = datetime.strptime(r["date"], "%Y-%m-%d").date()
            when = f"{self._day_word(d, self._now())} at {describe({**r, 'kind': 'daily', 'weekdays': []}).split(' at ')[1]}"
        return Reply(f"Okay! I'll remind you to {title[:1].lower() + title[1:]} {when.replace(':00 ', ' ')}.{self._phone()}", "happy")

    # ---- answers to my questions ----------------------------------------------------------------------
    def _answer(self, p: dict, t: str) -> Reply | None:
        a = p["action"]
        now = self._now()
        if a == "ask_title":
            title = clean_title(t)
            if not title:
                return None
            w, rest = parse_when(p["when_text"], now)
            return self._finish(title, w, now)
        if a == "ask_when":
            w, _ = parse_when(t, now)
            return self._finish(p["title"], w, now)
        if a == "ask_meridiem":
            low = t.lower()
            hint = "morning" if re.search(r"\b(morning|am|a\.m\.?|a m)\b", low) else "evening" if re.search(r"\b(evening|afternoon|night|pm|p\.m\.?|p m)\b", low) else None
            if not hint:
                return None
            w = p["w"]
            h, m = w.need_meridiem
            w.hm, w.need_meridiem = ((h % 12) + (0 if hint == "morning" else 12), m), None
            return self._finish(p["title"], w, now)
        if a == "ask_time":
            w2, _ = parse_when(t if re.search(r"\b(at|am|pm|:|noon|midnight|\d)\b", t, _I) else "at " + t, now)
            if w2.hm is None and w2.need_meridiem is None:
                return None
            w = p["w"]
            w.hm, w.need_meridiem = w2.hm, w2.need_meridiem
            return self._finish(p["title"], w, now)
        if a == "delete":
            y = _yes_no(t)
            if y is None:
                return None
            if not y:
                return Reply("Okay, I kept it.")
            return Reply("Done. I deleted that reminder." if self.rs.delete(p["id"]) else "I couldn't delete that reminder, so it is still there.")
        if a == "which":
            ids = p["ids"]
            words = re.findall(r"[a-z0-9']+", t.lower())
            idx = next((ORD[x] for x in words if x in ORD), None)
            if idx is not None and -len(ids) <= idx < len(ids):
                return self._apply(p["verb"], ids[idx])
            from .store import tokens
            q = set(tokens(t))
            hits = [i for i in ids if (r := self.rs.get(i)) and q and q <= set(tokens(r["title"]))]
            return self._apply(p["verb"], hits[0]) if len(hits) == 1 else None
        return None

    # ---- manage ---------------------------------------------------------------------------------------
    def _list(self) -> Reply:
        up = self.rs.upcoming(days=2)[:4]
        if not up:
            return Reply("You don't have any reminders coming up.")
        now = self._now()
        parts = []
        for u in up:
            d = datetime.fromtimestamp(u["at"], ZoneInfo(self.tz))
            parts.append(f"{u['title']}, {self._day_word(d.date(), now)} at {d.strftime('%-I:%M %p').replace(':00', '')}")
        return Reply("Coming up: " + "; ".join(parts) + ".")

    def _toggle(self, text: str, enable: bool) -> Reply:
        found = self.rs.find(text)
        if len(found) > 1:
            self._pend = {"t": self.clock(), "action": "which", "verb": "resume" if enable else "pause", "ids": [r["id"] for r in found[:4]]}
            return Reply("Which one: " + ", or ".join(r["title"] for r in found[:4]) + "?")
        return self._apply("resume" if enable else "pause", found[0]["id"])

    def _apply(self, verb: str, rid: str) -> Reply:
        try:
            if verb == "delete":
                return self._ask_delete_id(rid)
            r = self.rs.set_enabled(rid, verb == "resume")
        except StoreError:
            return Reply("I couldn't change that reminder, so nothing changed.")
        return Reply(f"Okay, {r['title'].lower()} is paused. Say resume to turn it back on." if verb == "pause" else f"Okay, {r['title'].lower()} is back on.")

    def _ask_delete(self, text: str) -> Reply:
        found = self.rs.find(text)
        if len(found) > 1:
            self._pend = {"t": self.clock(), "action": "which", "verb": "delete", "ids": [r["id"] for r in found[:4]]}
            return Reply("Which one: " + ", or ".join(r["title"] for r in found[:4]) + "?")
        return self._ask_delete_id(found[0]["id"])

    def _ask_delete_id(self, rid: str) -> Reply:
        r = self.rs.get(rid)
        self._pend = {"t": self.clock(), "action": "delete", "id": rid}
        return Reply(f"Do you want me to delete the reminder called {r['title']}? That can't be undone. Say yes to confirm.")

    # ---- right after a reminder was spoken ---------------------------------------------------------------
    def _context(self, ctx: dict, t: str) -> Reply | None:
        oid = ctx["id"]
        m = SNOOZE.match(t)
        if m:
            n = _num(m.group("n"), 0)
            unit = (m.group("u") or "").lower()
            if m.group("n") and "hour" in (m.group("n") or "").lower():
                minutes = 60
            elif unit.startswith("hour"):
                minutes = (n or 1) * 60
            elif n:
                minutes = n
            else:
                minutes = None                                                                 # her reminder's own snooze default
            try:
                o = self.rs.snooze(oid, minutes)
            except StoreError:
                return Reply("I couldn't snooze that one.")
            mins = round((o["due_at"] - self.clock()) / 60)
            self._ctx = {"id": o["id"], "t": self.clock()}
            return Reply(f"Okay, I'll remind you again in {mins} minute{'s' if mins != 1 else ''}.")
        if REMIND_ANY.search(t):                                                                # "remind me again at 5 pm"
            w, rest = parse_when(t, self._now())
            if clean_title(re.sub(r"\bagain\b", "", rest, flags=_I)) == "" and (w.hm or w.relative_minutes):
                if w.relative_minutes:
                    return self._snooze_to(oid, self.clock() + w.relative_minutes * 60)
                if w.hm:
                    today = self._now().replace(hour=w.hm[0], minute=w.hm[1], second=0, microsecond=0)
                    return self._snooze_to(oid, today.timestamp())
                return None
        if SKIP.match(t):
            o = self.rs.get_occurrence(oid)
            if o:
                try:
                    self.rs.acknowledge(oid)
                    r = self.rs.skip_next(o["reminder_id"])
                    return Reply("Okay, I'll skip the next one.")
                except StoreError:
                    return Reply("Okay, that one is dismissed.")
        if DISMISS.match(t):
            self.rs.acknowledge(oid)
            self._ctx = None
            return Reply("Okay!")
        return None

    def _snooze_to(self, oid: str, ts: float) -> Reply:
        try:
            o = self.rs.snooze(oid, until=ts)
        except StoreError as e:
            return Reply(f"I couldn't do that. {str(e)[:80]}.")
        d = datetime.fromtimestamp(o["due_at"], ZoneInfo(self.tz))
        return Reply(f"Okay, I'll remind you again at {d.strftime('%-I:%M %p').replace(':00', '')}.")
