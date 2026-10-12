"""A gentle daily check-in: once a day, at a time she chose, the robot asks how her day was and listens.

Her rules (each one has a test):
- OFF until she turns it on, and the first reply says exactly what it is. She can stop, pause, or move it by voice.
- At most one question a day, inside a window after her chosen time, and only when the robot is idle and awake, not in quiet mode,
  not right after a safety signal, and not on a low battery. A missed day is skipped. Ignored three times in a row, it goes quiet
  for two weeks by itself and never mentions it.
- It asks ONE question. It never asks what is wrong. If her answer sounds heavy it offers to just listen, and only starts if she says yes.
- Her answer is never stored. The only way anything is kept is her yes to "keep a note of that?", which saves her own words (and nothing
  the robot wrote) in the private notebook. Nobody else, including the owner, can read it from here.
- No guilt, no "I missed you", no count of days, no reminders. Declining ("not now") quiets it for two days.
- Everything it says is scripted; no language model sees her answer here."""
from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone, tzinfo
from pathlib import Path

SCHEMA_VERSION = 1
WINDOW_S = 3 * 3600.0
ANSWER_S = 10 * 60.0
DECLINE_DAYS = 2
IGNORE_LIMIT = 3
SNOOZE_DAYS = 14
QUESTIONS = ["How was your day?", "How's your day been so far?", "How are you doing today?"]
DISCLOSURE = ("Okay! Once a day I'll ask how your day was, and I'll just listen. You can say not now, change the time, or turn it off "
              "whenever you like. I don't keep what you tell me unless you ask me to save it, and anything you save stays private to you.")
_I = re.I
ON = re.compile(r"\b(turn on|start|enable|begin|i want|yes to) (the |my |a |some )?(daily )?(check[- ]?ins?|check in)\b", _I)
OFF = re.compile(r"\b(stop|turn off|disable|no more|cancel|end) (the |my )?(daily )?(check[- ]?ins?|checking in)\b|\bdon'?t check in\b", _I)
PAUSE = re.compile(r"\b(pause|snooze|mute) (the |my )?(daily )?(check[- ]?ins?)\b|\bno check[- ]?ins? (today|for now)\b", _I)
RESUME = re.compile(r"\b(resume|unpause|unmute) (the |my )?(daily )?(check[- ]?ins?)\b", _I)
SET_TIME = re.compile(r"\bcheck[- ]?ins?\b.*\b(at|to|for) (?P<h>\d{1,2})(:(?P<m>\d{2}))? ?(?P<ap>a\.?m\.?|p\.?m\.?)?", _I)
NOW = re.compile(r"\b(check in with me|ask me how my day (was|is|went)|how was my day)\b", _I)
DECLINE = re.compile(r"\b(not now|maybe later|later|not (really )?in the mood|don'?t (want|feel like)( to)? (talk|answer|say)|no thanks|leave me alone|skip (it|this))\b", _I)
HEAVY = re.compile(r"\b(rough|terrible|awful|horrible|stressful|stressed|exhausted|overwhelmed|upset|sad|anxious|worried|lonely|"
                   r"hard|tough|difficult|miserable|drained|awful|crying|cried|bad day|not (great|good|okay|ok|well))\b", _I)
NOT_BAD = re.compile(r"\bnot (that |too |so |all that )?(bad|terrible|awful)\b|\bnot a bad\b", _I)
YES = {"yes", "yeah", "yep", "sure", "ok", "okay", "please", "yup", "do", "listen", "go"}
NO = {"no", "nope", "nah", "not", "don't", "dont", "leave", "skip", "cancel", "stop"}
QUESTIONISH = re.compile(r"\?\s*$|^\W*(what|who|when|where|why|how|can you|could you|will you|please|set|turn|tell me|play|remind|call)\b", _I)


@dataclass
class CheckinReply:
    text: str
    style: str = "calm"
    paced: bool = False
    pauses: tuple = ()


class CheckIn:
    def __init__(self, path: str | Path, clock=time.time, tz: tzinfo = timezone.utc, quiet=lambda: False, psychology=None):
        self.path, self.clock, self.tz, self.quiet, self.psy = Path(path), clock, tz, quiet, psychology
        self.enabled, self.paused, self.time_hm = False, False, (19, 0)
        self.declined_until = self.snoozed_until = 0.0
        self.ignored = 0
        self.last_asked_day: str | None = None
        self._wait: dict | None = None                        # RAM only: {"stage","until","text"}
        self._n = 0
        self._load()

    # ---- persistence (settings only, never anything she said) ------------------------------------
    def _load(self) -> None:
        try:
            d = json.loads(self.path.read_text())
            if d.get("schema_version") != SCHEMA_VERSION:
                raise ValueError
            self.enabled, self.paused = bool(d["enabled"]), bool(d["paused"])
            h, m = d["time"]
            self.time_hm = (int(h) % 24, int(m) % 60)
            self.declined_until, self.snoozed_until = float(d["declined_until"]), float(d["snoozed_until"])
            self.ignored = max(0, min(IGNORE_LIMIT, int(d["ignored"])))
            self.last_asked_day = d.get("last_asked_day")
        except FileNotFoundError:
            pass
        except (OSError, ValueError, KeyError, TypeError):
            self.enabled = False                              # damaged file: stay off

    def _save(self) -> None:
        d = {"schema_version": SCHEMA_VERSION, "enabled": self.enabled, "paused": self.paused, "time": list(self.time_hm),
             "declined_until": self.declined_until, "snoozed_until": self.snoozed_until, "ignored": self.ignored,
             "last_asked_day": self.last_asked_day}
        tmp = self.path.with_suffix(".tmp")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(d, f)
        os.replace(tmp, self.path)

    def _midnight(self, now: float) -> float:
        return datetime.fromtimestamp(now, self.tz).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()

    def _day(self, now: float) -> str:
        return datetime.fromtimestamp(now, self.tz).strftime("%Y-%m-%d")

    # ---- scheduled question ----------------------------------------------------------------------
    def _expire(self, now: float) -> None:
        if self._wait and self._wait["stage"] == "answer" and now > self._wait["until"]:
            self._wait = None                                 # she did not answer: that is allowed, and counted only to back off
            self.ignored += 1
            if self.ignored >= IGNORE_LIMIT:
                self.snoozed_until, self.ignored = self._midnight(now) + SNOOZE_DAYS * 86400, 0
            self._save()
        elif self._wait and now > self._wait["until"]:
            self._wait = None

    def due(self, now: float) -> bool:
        self._expire(now)
        if not self.enabled or self.paused or self.quiet() or self._wait is not None:
            return False
        if now < self.declined_until or now < self.snoozed_until or self.last_asked_day == self._day(now):
            return False
        local = datetime.fromtimestamp(now, self.tz)
        start = local.replace(hour=self.time_hm[0], minute=self.time_hm[1], second=0, microsecond=0).timestamp()
        return start <= now < start + WINDOW_S

    def question(self, now: float) -> str | None:
        """The words to speak, or None if not due. Call asked() once the speech really started."""
        if not self.due(now):
            return None
        return QUESTIONS[int(now // 86400) % len(QUESTIONS)]

    def asked(self, now: float) -> None:
        self.last_asked_day = self._day(now)
        self._wait = {"stage": "answer", "until": now + ANSWER_S}
        self._save()

    def cancel(self) -> None:
        """A safety reply or a private exercise took over: drop whatever was waiting."""
        self._wait = None

    @property
    def waiting(self) -> bool:
        return self._wait is not None

    # ---- what she says ---------------------------------------------------------------------------
    def handle(self, utterance: str, psychology_busy: bool = False) -> CheckinReply | None:
        now = self.clock()
        t = utterance.strip()
        if not t:
            return None
        self._expire(now)
        ctl = self._control(t, now)
        if ctl is not None:
            return ctl
        w = self._wait
        if w is None or psychology_busy:
            if psychology_busy:
                self._wait = None
            return None
        words = set(re.findall(r"[a-z']+", t.lower()))
        if w["stage"] == "answer":
            self.ignored = 0
            if DECLINE.search(t):
                self._wait = None
                self.declined_until = self._midnight(now) + (DECLINE_DAYS + 1) * 86400      # the next two days are quiet
                self._save()
                return CheckinReply("No problem at all. We can talk another time.")
            if QUESTIONISH.search(t):
                self._wait = None
                self._save()
                return None                                   # she asked something else: ordinary conversation
            self._save()
            if HEAVY.search(t) and not NOT_BAD.search(t):
                self._wait = {"stage": "listen", "until": now + ANSWER_S}
                return CheckinReply("That sounds like a lot. Would you like me to just listen for a bit, or would you rather leave it? You can say yes or no.")
            if len(t.split()) >= 8:
                self._wait = {"stage": "save", "until": now + ANSWER_S, "text": t}
                return CheckinReply("Thank you for telling me. Would you like me to keep a note of that in your private journal? "
                                    "I would save exactly what you said. You can say yes or no.")
            self._wait = None
            return CheckinReply("Thanks for telling me.")
        yes, no = bool(words & YES), bool(words & NO)
        if w["stage"] == "listen":
            self._wait = None
            if yes and not no:
                pr = self.psy.start_listening() if self.psy is not None else None
                if pr is None:
                    return CheckinReply("I'm here. Tell me about it if you like.")
                return CheckinReply(pr.text, pr.style, paced=True, pauses=tuple(getattr(pr, "pauses", ()) or ()))
            if no:
                return CheckinReply("Okay. I'm here whenever you want to talk.")
            return None                                       # something else entirely: ordinary conversation
        if w["stage"] == "save":
            text = w["text"]
            self._wait = None
            if yes and not no:
                pr = self.psy.save_journal(text, self._day(now)) if self.psy is not None else None
                if pr is None:
                    return CheckinReply("To keep a private journal I need the reflection helper turned on, and it is off right now, so nothing was kept.")
                return CheckinReply(pr.text, pr.style)
            if no and not yes:
                return CheckinReply("Okay. I won't keep it, and it's gone as soon as we stop talking.")
            return None
        self._wait = None
        return None

    def _control(self, t: str, now: float) -> CheckinReply | None:
        if OFF.search(t):
            self.enabled, self._wait = False, None
            self._save()
            return CheckinReply("Okay, no more daily check-ins. You can turn them back on any time.")
        if ON.search(t):
            self.enabled, self.paused = True, False
            self._save()
            return CheckinReply(DISCLOSURE)
        if RESUME.search(t):
            self.paused, self.snoozed_until, self.declined_until = False, 0.0, 0.0
            self._save()
            return CheckinReply("Okay, the check-ins are back.")
        if PAUSE.search(t):
            self.paused, self._wait = True, None
            self._save()
            return CheckinReply("Okay, I'll pause the check-ins until you say resume.")
        m = SET_TIME.search(t)
        if m:
            h, mi, ap = int(m.group("h")), int(m.group("m") or 0), (m.group("ap") or "").lower().replace(".", "")
            if ap == "pm" and h < 12:
                h += 12
            if ap == "am" and h == 12:
                h = 0
            if not (0 <= h <= 23 and 0 <= mi <= 59) or (not ap and ":" not in t and h < 7):
                return CheckinReply("What time would you like it? For example, say 7 pm.")
            self.time_hm = (h, mi)
            self._save()
            return CheckinReply(f"Okay, I'll check in around {datetime(2000, 1, 1, h, mi).strftime('%-I:%M %p').replace(':00', '')}.")
        if NOW.search(t):
            self._wait = {"stage": "answer", "until": now + ANSWER_S}
            return CheckinReply("Sure. How was your day?")
        return None
