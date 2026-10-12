"""Her reminders: what to say, when, through which channel, and an honest record of what actually happened.

Design (each point has a test):
- One row per reminder (stable id). Editing changes the SAME row, so a recurring reminder can never be duplicated by editing it.
- Nothing is scheduled in advance. A cursor per reminder remembers the last due moment already handled; `tick(now)` creates an
  OCCURRENCE for every due moment between the cursor and now. An occurrence id is `reminder-id:due-timestamp`, so the same moment can
  never produce two occurrences, whatever restarts, retries or clock changes happen.
- Delivery is a state, not a promise: the robot channel is `pending` until the robot really spoke it (`spoken`), and becomes
  `unavailable` if it could not within the grace period (asleep, offline, busy, quiet hours). The phone channel is `pending` until she
  sees it in the app. If neither happened by the end of the grace period the occurrence is `missed` and is shown, not spoken later.
- Overdue reminders are never replayed in a burst: only the newest due moment of a reminder stays deliverable.
- Spoken words come from her wording or from scripted lines in her chosen style; the schedule is never touched by personality.
No language model is involved anywhere in this file."""
from __future__ import annotations

import json
import re
import string
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

from . import schedule as sch
from .store import ProductivityStore, StoreError

STYLES = ("auto", "plain", "direct", "gentle", "playful", "quiet")
PRIORITIES = ("low", "normal", "high")
ROBOT = ("n/a", "pending", "speaking", "spoken", "unavailable", "quiet", "failed")
PHONE = ("n/a", "pending", "shown")
STATUS = ("pending", "delivered", "acknowledged", "snoozed", "skipped", "missed")
MAX_REMINDERS = 200
MAX_CATCHUP = 60                       # how many overdue moments one tick will record (the rest are dropped, not replayed)
ALLOWED_FIELDS = {"title", "name", "time"}

CATS = [("food", r"\b(eat|lunch|dinner|breakfast|snack|meal|food|hungry)\b"), ("water", r"\b(water|hydrat\w*|drink)\b"),
        ("exercise", r"\b(exercis\w*|workout|gym|run|walk|stretch\w*|yoga|move)\b"), ("study", r"\b(study|learn\w*|russian|practice|lesson|read\w*)\b"),
        ("project", r"\b(project|build|robot|code|coding|paint\w*|draw\w*|write|writing|create)\b"), ("bed", r"\b(bed|bedtime|sleep|wind down)\b"),
        ("break", r"\b(break|rest|relax\w*)\b"), ("call", r"\b(call|phone|text|message|ring)\b")]
LINES = {
    "gentle": {
        "food": ["Hey! It's {t}. Want to grab something to eat?", "Hi! It's {t}. Would a little snack or a meal be nice right now?", "It's {t}. If you haven't eaten in a while, this could be a good moment."],
        "water": ["Hey, it's {t}. Want to have a glass of water?", "A little reminder to drink some water, if you'd like.", "Water time, if you're up for it."],
        "exercise": ["Hey, it's {t}. Want to move a little together?", "It's exercise time. Want to do a little workout?", "Ready to stretch or move a bit?"],
        "study": ["Hey! It's {t}. Ready for some study time?", "It's {t}. Want to practise a little now?", "You planned some learning about now. Shall we start?"],
        "project": ["Hey! You planned to work on your project around now. Want to get started?", "It's {t}. Want to spend a little time on your project?", "Project time, whenever you're ready."],
        "bed": ["It's getting close to your planned bedtime. Want to start winding down?", "It's {t}. Want to start getting ready for bed?", "A gentle nudge: your planned bedtime is near."],
        "break": ["Hey, it's {t}. Want to take a little break?", "Time for a short rest, if you'd like one.", "It's {t}. A pause might feel nice."],
        "call": ["It's {t}. Time for the call or message you planned.", "Hey! You planned to get in touch with someone around now.", "Here's your reminder to make that call."],
        "generic": ["Hey! It's {t}. Reminder: {title}. Want to get started?", "Hi! A reminder for {title}.", "It's {t}, time for {title}."]},
    "playful": {
        "food": ["Snack o'clock! It's {t}. Want to grab something tasty?", "Hey hey! It's {t}. Shall we find something yummy to eat?", "It's {t}! Time to refuel, if you feel like it."],
        "water": ["Splash! Time for some water. Want a glass?", "Hey! A tall glass of water is calling. It's {t}.", "Hydration check, if you like!"],
        "exercise": ["It's exercise time! Want to do a little workout together?", "Ooh, it's {t}! Let's move a little!", "Stretch time! Ready when you are."],
        "study": ["Ready for our study time? It's {t}!", "Hey! Let's learn something. Shall we start?", "It's {t}! Study time, ready?"],
        "project": ["Hey! You planned to work on your project around now. Want to get started?", "Project time! What shall we build today?", "It's {t}! Your project is waiting, whenever you are."],
        "bed": ["It's almost bedtime! Want to start winding down?", "Sleepy-time is coming. Shall we start winding down?", "It's {t}. Time to start getting cosy for bed."],
        "break": ["Break time! It's {t}. Want to rest a little?", "Hey! A tiny break could be nice right now.", "It's {t}. Time to pause for a moment!"],
        "call": ["It's {t}! Time to make that call.", "Ring ring! You planned to call someone around now.", "Hey! Your call is on the plan for now."],
        "generic": ["Hey! It's {t}! Reminder: {title}. Ready?", "Ooh, it's {t}. Time for {title}!", "Hi hi! Here's your reminder: {title}."]},
}


def category(title: str) -> str:
    for name, pat in CATS:
        if re.search(pat, title, re.I):
            return name
    return "generic"


def when_phrase(ts: float, tz: str) -> str:
    t = datetime.fromtimestamp(ts, ZoneInfo(tz))
    return "around " + t.strftime("%-I:%M %p").replace(":00", "")


def check_template(text: str) -> str:
    text = (text or "").strip()
    if len(text) > 200:
        raise StoreError("the spoken message is too long (200 characters)")
    for _, field, _, _ in string.Formatter().parse(text):
        if field is not None and field not in ALLOWED_FIELDS:
            raise StoreError("the message can use only {title}, {name} and {time}")
    return text


def compose(rem: dict, due_at: float, name: str | None, auto_style: str = "gentle", variant: int | None = None) -> str:
    """The words Nine Tails says. Her own message wins; otherwise a scripted line in her chosen style."""
    style = rem["style"] if rem["style"] != "auto" else auto_style
    t = when_phrase(due_at, rem["tz"])
    fields = {"title": rem["title"], "name": name or "friend", "time": t}
    if rem["message"]:
        return rem["message"].format(**fields).strip()
    if style == "plain":
        return f"Reminder: {rem['title']}."
    if style == "direct":
        return f"It's {t}. {rem['title']}."
    pool = LINES.get(style, LINES["gentle"])
    lines = pool.get(category(rem["title"]), pool["generic"])
    i = int(due_at // 86400) + (int(rem["id"][:4], 16) if rem.get("id") else 0) if variant is None else variant
    return lines[i % len(lines)].format(t=t, title=rem["title"])


class ReminderStore:
    def __init__(self, store: ProductivityStore, clock, default_tz: str = "UTC"):
        self.s, self.clock, self.default_tz = store, clock, default_tz
        with store._lock:
            store.db.executescript("""
            CREATE TABLE IF NOT EXISTS reminders (id TEXT PRIMARY KEY, title TEXT NOT NULL, message TEXT NOT NULL, style TEXT NOT NULL,
              kind TEXT NOT NULL, rtime TEXT, rdate TEXT, weekdays TEXT NOT NULL, interval_minutes INTEGER, window_start TEXT, window_end TEXT,
              start_date TEXT, end_date TEXT, tz TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1, completed INTEGER NOT NULL DEFAULT 0,
              robot INTEGER NOT NULL DEFAULT 1, phone INTEGER NOT NULL DEFAULT 1, snooze_minutes INTEGER NOT NULL DEFAULT 10,
              grace_minutes INTEGER NOT NULL DEFAULT 30, priority TEXT NOT NULL DEFAULT 'normal', cursor_at REAL NOT NULL,
              created_at REAL NOT NULL, modified_at REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS occurrences (id TEXT PRIMARY KEY, reminder_id TEXT NOT NULL, due_at REAL NOT NULL, status TEXT NOT NULL,
              robot TEXT NOT NULL, phone TEXT NOT NULL, spoken_text TEXT, updated_at REAL NOT NULL, snooze_of TEXT);
            CREATE INDEX IF NOT EXISTS idx_occ_rem ON occurrences(reminder_id, due_at);""")

    # ---- rows --------------------------------------------------------------------------------------
    def _rem(self, r) -> dict:
        i = r["id"]
        d = {"id": i, "title": self.s._open(r["title"], f"r:{i}:title"), "message": self.s._open(r["message"], f"r:{i}:message"),
             "style": r["style"], "kind": r["kind"], "time": r["rtime"], "date": r["rdate"], "weekdays": json.loads(r["weekdays"]),
             "interval_minutes": r["interval_minutes"], "window_start": r["window_start"], "window_end": r["window_end"],
             "start_date": r["start_date"], "end_date": r["end_date"], "tz": r["tz"], "enabled": bool(r["enabled"]),
             "completed": bool(r["completed"]), "channels": {"robot": bool(r["robot"]), "phone": bool(r["phone"])},
             "snooze_minutes": r["snooze_minutes"], "grace_minutes": r["grace_minutes"], "priority": r["priority"],
             "cursor_at": r["cursor_at"], "created_at": r["created_at"], "modified_at": r["modified_at"]}
        return d

    def _occ(self, r) -> dict:
        return {"id": r["id"], "reminder_id": r["reminder_id"], "due_at": r["due_at"], "status": r["status"], "robot": r["robot"],
                "phone": r["phone"], "spoken_text": self.s._open(r["spoken_text"], f"o:{r['id']}:text") if r["spoken_text"] else None,
                "updated_at": r["updated_at"], "snooze_of": r["snooze_of"]}

    def get(self, rid: str) -> dict | None:
        with self.s._lock:
            r = self.s.db.execute("SELECT * FROM reminders WHERE id=?", (rid,)).fetchone()
            return self._decorate(self._rem(r)) if r else None

    def _decorate(self, d: dict) -> dict:
        now = self.clock()
        d["schedule_text"] = sch.describe(d)
        d["next_at"] = self.next_at(d, now)
        d["status"] = "completed" if d["completed"] else "paused" if not d["enabled"] else "scheduled" if d["next_at"] else "ended"
        last = self.s.db.execute("SELECT * FROM occurrences WHERE reminder_id=? AND snooze_of IS NULL ORDER BY due_at DESC LIMIT 1", (d["id"],)).fetchone()
        d["last_delivery"] = self._delivery_text(self._occ(last)) if last else None
        d["last_occurrence"] = self._occ(last) if last else None
        return d

    @staticmethod
    def _delivery_text(o: dict) -> str:
        parts = []
        if o["robot"] == "spoken":
            parts.append("spoken by Nine Tails")
        elif o["robot"] in ("unavailable", "failed"):
            parts.append("Nine Tails could not say it")
        elif o["robot"] == "quiet":
            parts.append("quiet hours, not spoken")
        elif o["robot"] in ("pending", "speaking"):
            parts.append("waiting for Nine Tails")
        if o["phone"] == "shown":
            parts.append("seen on your phone")
        elif o["phone"] == "pending":
            parts.append("not seen on your phone yet")
        return {"missed": "Missed: ", "skipped": "Skipped: ", "acknowledged": "Done: ", "snoozed": "Snoozed: "}.get(o["status"], "") + (", ".join(parts) or "nothing to deliver")

    def next_at(self, rem: dict, now: float) -> float | None:
        if rem["completed"] or not rem["enabled"]:
            return None
        return sch.next_due(rem, max(now, rem["cursor_at"]))

    def reminders(self) -> list[dict]:
        with self.s._lock:
            return [self._decorate(self._rem(r)) for r in self.s.db.execute("SELECT * FROM reminders ORDER BY created_at")]

    # ---- create / edit ----------------------------------------------------------------------------
    def _clean(self, f: dict, base: dict | None = None) -> dict:
        b = dict(base or {"style": "auto", "message": "", "weekdays": [], "tz": self.default_tz, "snooze_minutes": 10, "grace_minutes": 30,
                          "priority": "normal", "channels": {"robot": True, "phone": True}, "kind": "daily", "time": "09:00"})
        for k, v in f.items():
            if k in ("channels",):
                if not isinstance(v, dict) or set(v) - {"robot", "phone"} or not all(isinstance(x, bool) for x in v.values()):
                    raise StoreError("channels are robot and phone, each true or false")
                b["channels"] = {**b["channels"], **v}
            elif k in ("title", "message", "style", "kind", "time", "date", "weekdays", "interval_minutes", "window_start", "window_end",
                       "start_date", "end_date", "tz", "snooze_minutes", "grace_minutes", "priority"):
                b[k] = v
        title = re.sub(r"\s+", " ", str(b.get("title") or "")).strip()
        if not title or len(title) > 80:
            raise StoreError("a reminder needs a title of 1 to 80 characters")
        if b["style"] not in STYLES or b["priority"] not in PRIORITIES:
            raise StoreError("unknown style or priority")
        for k, lo, hi in (("snooze_minutes", 1, 240), ("grace_minutes", 5, 240)):
            if isinstance(b[k], bool) or not isinstance(b[k], int) or not lo <= b[k] <= hi:
                raise StoreError(f"{k.replace('_', ' ')} must be {lo} to {hi}")
        b["title"], b["message"] = title, check_template(b.get("message") or "")
        try:
            sch.validate({**b, "weekdays": b.get("weekdays") or []})
        except sch.ScheduleError as e:
            raise StoreError(str(e)) from None
        b["weekdays"] = sorted(set(b.get("weekdays") or []))
        return b

    def create(self, **f) -> dict:
        b = self._clean(f)
        with self.s._lock:
            if self.s.db.execute("SELECT COUNT(*) FROM reminders").fetchone()[0] >= MAX_REMINDERS:
                raise StoreError("too many reminders")
            i, now = uuid.uuid4().hex[:12], self.clock()
            self.s.db.execute("""INSERT INTO reminders(id,title,message,style,kind,rtime,rdate,weekdays,interval_minutes,window_start,window_end,start_date,end_date,tz,
                                 enabled,completed,robot,phone,snooze_minutes,grace_minutes,priority,cursor_at,created_at,modified_at)
                                 VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,0,?,?,?,?,?,?,?,?)""",
                              (i, self.s._seal(b["title"], f"r:{i}:title"), self.s._seal(b["message"], f"r:{i}:message"), b["style"], b["kind"],
                               b.get("time"), b.get("date"), json.dumps(b["weekdays"]), b.get("interval_minutes"), b.get("window_start"), b.get("window_end"),
                               b.get("start_date") or None, b.get("end_date") or None, b["tz"], int(b["channels"]["robot"]), int(b["channels"]["phone"]),
                               b["snooze_minutes"], b["grace_minutes"], b["priority"], now, now, now))
            self.s.db.commit()
            return self.get(i)

    def update(self, rid: str, **f) -> dict:
        with self.s._lock:
            cur = self.get(rid)
            if cur is None:
                raise StoreError("no such reminder")
            b = self._clean(f, {k: cur[k] for k in ("title", "message", "style", "kind", "time", "date", "weekdays", "interval_minutes", "window_start", "window_end",
                                                      "start_date", "end_date", "tz", "snooze_minutes", "grace_minutes", "priority", "channels")})
            sched_keys = ("kind", "time", "date", "weekdays", "interval_minutes", "window_start", "window_end", "start_date", "end_date", "tz")
            changed = any(b.get(k) != cur.get(k) for k in sched_keys)
            now = self.clock()
            cursor = now if changed else cur["cursor_at"]                           # a new schedule starts from now: no replay, no duplicates
            completed = 0 if changed else int(cur["completed"])
            self.s.db.execute("""UPDATE reminders SET title=?,message=?,style=?,kind=?,rtime=?,rdate=?,weekdays=?,interval_minutes=?,window_start=?,window_end=?,
                                 start_date=?,end_date=?,tz=?,robot=?,phone=?,snooze_minutes=?,grace_minutes=?,priority=?,cursor_at=?,completed=?,modified_at=? WHERE id=?""",
                              (self.s._seal(b["title"], f"r:{rid}:title"), self.s._seal(b["message"], f"r:{rid}:message"), b["style"], b["kind"], b.get("time"),
                               b.get("date"), json.dumps(b["weekdays"]), b.get("interval_minutes"), b.get("window_start"), b.get("window_end"),
                               b.get("start_date") or None, b.get("end_date") or None, b["tz"], int(b["channels"]["robot"]), int(b["channels"]["phone"]),
                               b["snooze_minutes"], b["grace_minutes"], b["priority"], cursor, completed, now, rid))
            self.s.db.commit()
            return self.get(rid)

    def set_enabled(self, rid: str, enabled: bool) -> dict:
        with self.s._lock:
            if self.get(rid) is None:
                raise StoreError("no such reminder")
            now = self.clock()
            self.s.db.execute("UPDATE reminders SET enabled=?, cursor_at=?, modified_at=? WHERE id=?", (int(enabled), now, now, rid))   # resuming never replays the pause
            self.s.db.commit()
            return self.get(rid)

    def duplicate(self, rid: str) -> dict:
        cur = self.get(rid)
        if cur is None:
            raise StoreError("no such reminder")
        keys = ("title", "message", "style", "kind", "time", "date", "weekdays", "interval_minutes", "window_start", "window_end", "start_date", "end_date",
                "tz", "snooze_minutes", "grace_minutes", "priority", "channels")
        f = {k: cur[k] for k in keys}
        f["title"] = (cur["title"] + " (copy)")[:80]
        return self.create(**f)

    def delete(self, rid: str) -> bool:
        with self.s._lock:
            self.s.db.execute("DELETE FROM occurrences WHERE reminder_id=?", (rid,))
            n = self.s.db.execute("DELETE FROM reminders WHERE id=?", (rid,)).rowcount
            self.s.db.commit()
            return bool(n)

    def find(self, text: str) -> list[dict]:
        from .store import tokens
        q = set(tokens(text))
        return [r for r in self.reminders() if q and q <= set(tokens(r["title"]))]

    # ---- settings -----------------------------------------------------------------------------------
    def settings(self) -> dict:
        g = self.s.setting
        return {"speak": g("rem_speak", "on") == "on", "phone": g("rem_phone", "on") == "on", "fallback": g("rem_fallback", "phone"),
                "style": g("rem_style", "auto"), "quiet_start": g("rem_quiet_start", ""), "quiet_end": g("rem_quiet_end", ""), "tz": g("rem_tz", self.default_tz)}

    def set_settings(self, **f) -> dict:
        for k, v in f.items():
            if k in ("speak", "phone") and isinstance(v, bool):
                self.s.set_setting("rem_" + k, "on" if v else "off")
            elif k == "fallback" and v in ("phone", "skip"):
                self.s.set_setting("rem_fallback", v)
            elif k == "style" and v in STYLES:
                self.s.set_setting("rem_style", v)
            elif k in ("quiet_start", "quiet_end"):
                if v:
                    try:
                        sch.parse_hm(v)
                    except sch.ScheduleError as e:
                        raise StoreError(str(e)) from None
                self.s.set_setting("rem_" + k, v or "")
            elif k == "tz":
                try:
                    ZoneInfo(v)
                except Exception:
                    raise StoreError("unknown time zone") from None
                self.s.set_setting("rem_tz", v)
            else:
                raise StoreError(f"unknown setting {k}")
        return self.settings()

    def in_quiet_hours(self, ts: float) -> bool:
        st = self.settings()
        if not st["quiet_start"] or not st["quiet_end"]:
            return False
        t = datetime.fromtimestamp(ts, ZoneInfo(st["tz"]))
        a, b = sch.parse_hm(st["quiet_start"]), sch.parse_hm(st["quiet_end"])
        cur = t.time().replace(second=0, microsecond=0)
        return (a <= cur < b) if a < b else (cur >= a or cur < b)

    # ---- the clock tick ------------------------------------------------------------------------------
    def tick(self, now: float | None = None) -> list[dict]:
        """Records every due moment up to `now` as an occurrence, ends stale ones. Safe to call as often as you like."""
        now = self.clock() if now is None else now
        created: list[dict] = []
        with self.s._lock:
            st = self.settings()
            for row in self.s.db.execute("SELECT * FROM reminders WHERE enabled=1 AND completed=0").fetchall():
                rem = self._rem(row)
                cursor, made = rem["cursor_at"], 0
                while made < MAX_CATCHUP:
                    nxt = sch.next_due(rem, cursor)
                    if nxt is None:
                        if rem["kind"] == "once":
                            self.s.db.execute("UPDATE reminders SET completed=1 WHERE id=?", (rem["id"],))
                        break
                    if nxt > now:
                        break
                    oid = f"{rem['id']}:{int(nxt)}"
                    quiet = self.in_quiet_hours(nxt)
                    robot = "n/a" if not (rem["channels"]["robot"] and st["speak"]) or rem["style"] == "quiet" else "quiet" if quiet else "pending"
                    phone = "pending" if rem["channels"]["phone"] and st["phone"] else "n/a"
                    cur = self.s.db.execute("INSERT OR IGNORE INTO occurrences(id,reminder_id,due_at,status,robot,phone,updated_at) VALUES(?,?,?,?,?,?,?)",
                                            (oid, rem["id"], nxt, "pending", robot, phone, now))
                    if cur.rowcount:
                        created.append(self._occ(self.s.db.execute("SELECT * FROM occurrences WHERE id=?", (oid,)).fetchone()))
                    cursor, made = nxt, made + 1
                self.s.db.execute("UPDATE reminders SET cursor_at=? WHERE id=?", (cursor, rem["id"]))
            self._expire(now)
            self.s.db.commit()
        return created

    def _expire(self, now: float) -> None:
        rows = self.s.db.execute("""SELECT o.*, r.grace_minutes AS g FROM occurrences o JOIN reminders r ON r.id=o.reminder_id
                                    WHERE o.status='pending'""").fetchall()
        newest = {r["reminder_id"]: r["m"] for r in self.s.db.execute(
            "SELECT reminder_id, MAX(due_at) AS m FROM occurrences WHERE snooze_of IS NULL AND status!='skipped' GROUP BY reminder_id")}
        for r in rows:
            stale = now - r["due_at"] > r["g"] * 60
            superseded = r["snooze_of"] is None and r["due_at"] < newest.get(r["reminder_id"], 0)
            if not (stale or superseded):
                continue
            robot = "unavailable" if r["robot"] == "pending" else r["robot"]
            status = "delivered" if (r["robot"] == "spoken" or r["phone"] == "shown") else "missed"
            self.s.db.execute("UPDATE occurrences SET status=?, robot=?, updated_at=? WHERE id=?", (status, robot, now, r["id"]))

    # ---- delivery ------------------------------------------------------------------------------------
    def occurrences(self, limit: int = 50, reminder_id: str | None = None) -> list[dict]:
        with self.s._lock:
            q = "SELECT * FROM occurrences" + (" WHERE reminder_id=?" if reminder_id else "") + " ORDER BY due_at DESC LIMIT ?"
            return [self._occ(r) for r in self.s.db.execute(q, ((reminder_id, limit) if reminder_id else (limit,)))]

    def get_occurrence(self, oid: str) -> dict | None:
        with self.s._lock:
            r = self.s.db.execute("SELECT * FROM occurrences WHERE id=?", (oid,)).fetchone()
            return self._occ(r) if r else None

    def due_for_robot(self, now: float | None = None) -> list[dict]:
        """Occurrences the robot may speak right now: still within their grace period, robot channel pending. Highest priority, then oldest."""
        now = self.clock() if now is None else now
        out = []
        with self.s._lock:
            rows = self.s.db.execute("""SELECT o.*, r.grace_minutes AS g, r.priority AS pr FROM occurrences o JOIN reminders r ON r.id=o.reminder_id
                                        WHERE o.status='pending' AND o.robot='pending' AND r.enabled=1""").fetchall()
            for r in rows:
                if r["due_at"] <= now <= r["due_at"] + r["g"] * 60:
                    out.append((("high", "normal", "low").index(r["pr"]), r["due_at"], self._occ(r)))
        return [o for _, _, o in sorted(out, key=lambda t: (t[0], t[1]))]

    def mark_robot(self, oid: str, state: str, spoken_text: str | None = None) -> None:
        if state not in ROBOT:
            raise StoreError("bad robot state")
        with self.s._lock:
            o = self.get_occurrence(oid)
            if o is None:
                return
            now = self.clock()
            status = "delivered" if state == "spoken" and o["status"] == "pending" else o["status"]
            self.s.db.execute("UPDATE occurrences SET robot=?, status=?, spoken_text=COALESCE(?, spoken_text), updated_at=? WHERE id=?",
                              (state, status, self.s._seal(spoken_text, f"o:{oid}:text") if spoken_text else None, now, oid))
            self.s.db.commit()

    def phone_pending(self, now: float | None = None) -> list[dict]:
        """What the app should show: due and not yet seen (and, if she wants, missed ones that were never seen)."""
        now = self.clock() if now is None else now
        fb = self.settings()["fallback"]
        with self.s._lock:
            rows = [self._occ(r) for r in self.s.db.execute("SELECT * FROM occurrences WHERE phone='pending' AND status IN ('pending','missed','delivered') ORDER BY due_at")]
        out = []
        for o in rows:
            if o["status"] == "missed" and fb == "skip":
                continue
            rem = self.get(o["reminder_id"])
            out.append({**o, "title": rem["title"] if rem else "Reminder", "text": o["spoken_text"] or (compose(rem, o["due_at"], None) if rem else "")})
        return out

    def mark_phone_seen(self, oid: str) -> bool:
        with self.s._lock:
            o = self.get_occurrence(oid)
            if o is None or o["phone"] != "pending":
                return False
            status = "delivered" if o["status"] == "pending" else o["status"]
            self.s.db.execute("UPDATE occurrences SET phone='shown', status=?, updated_at=? WHERE id=?", (status, self.clock(), oid))
            self.s.db.commit()
            return True

    def acknowledge(self, oid: str) -> bool:
        with self.s._lock:
            o = self.get_occurrence(oid)
            if o is None:
                return False
            self.s.db.execute("UPDATE occurrences SET status='acknowledged', phone=CASE WHEN phone='pending' THEN 'shown' ELSE phone END, "
                              "robot=CASE WHEN robot='pending' THEN 'unavailable' ELSE robot END, updated_at=? WHERE id=?", (self.clock(), oid))
            self.s.db.commit()
            return True

    def snooze(self, oid: str, minutes: int | None = None, until: float | None = None) -> dict:
        """Dismisses this occurrence and makes ONE new one later (a snooze of a snooze replaces it, never stacks)."""
        with self.s._lock:
            o = self.get_occurrence(oid)
            rem = self.get(o["reminder_id"]) if o else None
            if o is None or rem is None:
                raise StoreError("no such reminder")
            now = self.clock()
            minutes = rem["snooze_minutes"] if minutes is None else minutes
            if until is None and (isinstance(minutes, bool) or not isinstance(minutes, int) or not 1 <= minutes <= 24 * 60):
                raise StoreError("snooze 1 minute to 24 hours")
            due = until if until is not None else now + minutes * 60
            if due <= now:
                raise StoreError("that time has already passed")
            root = o["snooze_of"] or o["id"]
            n = self.s.db.execute("SELECT COUNT(*) FROM occurrences WHERE snooze_of=?", (root,)).fetchone()[0] + 1
            new_id = f"{root}~s{n}"
            self.s.db.execute("UPDATE occurrences SET status='snoozed', updated_at=? WHERE id=?", (now, oid))
            self.s.db.execute("INSERT OR IGNORE INTO occurrences(id,reminder_id,due_at,status,robot,phone,updated_at,snooze_of) VALUES(?,?,?,?,?,?,?,?)",
                              (new_id, rem["id"], due, "pending", "pending" if rem["channels"]["robot"] and self.settings()["speak"] and rem["style"] != "quiet" else "n/a",
                               "pending" if rem["channels"]["phone"] and self.settings()["phone"] else "n/a", now, root))
            self.s.db.commit()
            return self.get_occurrence(new_id)

    def skip_next(self, rid: str) -> dict:
        """Skip the next occurrence: it is recorded as skipped when its moment comes, and nothing is said."""
        with self.s._lock:
            rem = self.get(rid)
            if rem is None or rem["next_at"] is None:
                raise StoreError("nothing to skip")
            nxt = rem["next_at"]
            self.s.db.execute("INSERT OR IGNORE INTO occurrences(id,reminder_id,due_at,status,robot,phone,updated_at) VALUES(?,?,?,?,?,?,?)",
                              (f"{rid}:{int(nxt)}", rid, nxt, "skipped", "n/a", "n/a", self.clock()))
            self.s.db.execute("UPDATE reminders SET cursor_at=? WHERE id=?", (nxt, rid))
            self.s.db.commit()
            return self.get(rid)

    def upcoming(self, now: float | None = None, days: int = 7) -> list[dict]:
        now = self.clock() if now is None else now
        out = []
        for r in self.reminders():
            if not r["enabled"] or r["completed"]:
                continue
            for t in sch.upcoming(r, max(now, 0), 50, until=now + days * 86400):
                out.append({"reminder_id": r["id"], "title": r["title"], "at": t, "tz": r["tz"]})
        return sorted(out, key=lambda x: x["at"])

    def export(self) -> dict:
        return {"reminders": self.reminders(), "occurrences": self.occurrences(100000), "settings": self.settings()}
