"""Daily Psychology Discoveries: delivery, preferences, follow-ups (spec 51, 52, 50, 54).

What this guarantees (each one has a test):
- OFF until she turns it on, and she is told plainly what it is when she does.
- Only VERIFIED library entries are ever offered; the wording of every claim comes from the entry, never from a model.
- At most one scheduled insight per day, inside a window after her chosen time; a missed day is simply skipped.
  Ignoring an insight changes nothing: no reminder, no repeat, no counter that grows.
- No scores: the state file holds only what was shown, saved or skipped, plus her settings. Never her words.
- Personalization is limited to topics she names herself. Nothing is chosen from what she has told the robot privately.
- Sensitive-topic entries (relationships, conflict) are served only if she asked for them.
- Every follow-up is scripted from the entry: sources, a tiny exercise, what the research cannot tell us, skip, save, revisit."""
from __future__ import annotations

import json
import os
import random
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone, tzinfo
from pathlib import Path

from .library import CATEGORIES, Entry, script_for

SCHEMA_VERSION = 1
WINDOW_S = 4 * 3600.0              # a scheduled insight may start up to 4 hours after the chosen time, then the day is skipped
CONTEXT_S = 15 * 60.0              # "the source?" / "skip" only refer to an insight shown in the last 15 minutes
INBOX_MAX = 30
MODES = ("voice", "text", "both")
TOPIC_WORDS = {"habits_and_behavior_change": ("habit", "habits", "routine", "routines", "behavior change"),
               "self_awareness": ("self awareness", "self-awareness", "awareness", "assumptions"),
               "emotional_regulation": ("emotion", "emotions", "feelings", "regulation", "stress"),
               "openness_to_learning": ("learning", "motivation", "advice"),
               "resilience_and_adaptation": ("resilience", "adapting", "adaptation", "self compassion", "self-compassion", "mistakes"),
               "relationships_and_conflict": ("relationship", "relationships", "conflict", "communication"),
               "goals_and_personal_systems": ("goals", "goal", "planning", "systems"),
               "identity_and_growth": ("identity", "growth", "values")}
_I = re.I
GIVE = re.compile(r"\b(give|tell|show|share) me (a|an|one|today'?s|another|the next) (psychology |new )?(discovery|insight)\b|\bpsychology (discovery|fact)\b|\bdaily (psychology )?(discovery|insight)s? now\b", _I)
ANOTHER = re.compile(r"^\W*(another|one more|next)( one| insight| discovery)?\W*$", _I)
SOURCE = re.compile(r"\b(source|sources|where (is|does|did) (that|this|it) (come|from)|where did you get that|which (study|research)|the (study|research|paper))\b", _I)
EXERCISE = re.compile(r"\b(tiny |small |little )?exercise\b|\btry it\b|\bshow me how\b", _I)
DEEPER = re.compile(r"\b(tell me more|go deeper|explore (it|this|that)|more (details|about (it|this|that))|other side|opposite (perspective|view)|what (can'?t|cannot|doesn'?t) (it|this) (tell|say)|limits?|limitations)\b", _I)
SKIP = re.compile(r"^\W*(skip|skip (it|this|that|this one)|next|not now|no thanks|no thank you|not interested)\W*$", _I)
SAVE = re.compile(r"\b(save|keep|bookmark|remember) (this|that|it)( one| insight| discovery)?\b", _I)
LIST_SAVED = re.compile(r"\b(what|which) (insights|discoveries) (have i|did i|do i have) saved\b|\bmy saved (insights|discoveries)\b|\bread my saved\b", _I)
REVISIT = re.compile(r"\b(revisit|replay|read again|say again|repeat)\b (?P<what>.{0,80})", _I)
OFF = re.compile(r"\b(stop|turn off|disable|no more|cancel|end) (the |my )?(daily )?(psychology )?(insights?|discoveries|discovery)\b|\bdon'?t (send|give|tell) me (any )?(daily )?(insights?|discoveries)\b", _I)
ON = re.compile(r"\b(turn on|start|enable|begin|i want|sign me up for) (the |my |some )?(daily )?(psychology )?(insights?|discoveries|discovery)\b", _I)
PAUSE = re.compile(r"\b(pause|quiet|mute|snooze) (the |my )?(daily )?(psychology )?(insights?|discoveries)\b|\bno (insights?|discoveries) (today|for now)\b", _I)
RESUME = re.compile(r"\b(resume|unpause|unmute) (the |my )?(daily )?(psychology )?(insights?|discoveries)\b", _I)
SET_TIME = re.compile(r"\b(set|change|move|send|deliver)\b.*\b(insights?|discoveries)\b.*\b(at|to|for) (?P<h>\d{1,2})(:(?P<m>\d{2}))? ?(?P<ap>a\.?m\.?|p\.?m\.?)?", _I)
SET_MODE = re.compile(r"\b(insights?|discoveries)\b.*\b(as |in |by )?(?P<mode>text|voice|both)\b", _I)
ONLY_TOPIC = re.compile(r"\b(only|just) (?P<t>[a-z\- ]{3,30}) (insights?|discoveries|topics?)\b", _I)
ALL_TOPICS = re.compile(r"\b(all|any|every) (topics?|kinds?)\b.*\b(insights?|discoveries)\b|\b(insights?|discoveries) (on|about) (all|any) topics?\b", _I)
SENSITIVE_ON = re.compile(r"\b(include|add|allow) (the )?(relationship|conflict|communication) (topics?|insights?|discoveries)\b", _I)
SENSITIVE_OFF = re.compile(r"\b(skip|no|stop|without) (the )?(relationship|conflict) (topics?|insights?|discoveries)\b", _I)

DISCLOSURE = ("Okay! Each day I'll share one short psychology discovery from real research, about a minute long. "
              "You can skip any of them, ask where one comes from, change the time, or turn them off whenever you like.")


@dataclass
class Script:
    entry: Entry
    text: str
    scheduled: bool


class DailyInsights:
    def __init__(self, path: str | Path, entries: list[Entry], clock=time.time, tz: tzinfo = timezone.utc,
                 quiet=lambda: False, rng: random.Random | None = None, require_owner_confirmed: bool = False):
        self.path, self.clock, self.tz, self.quiet = Path(path), clock, tz, quiet
        self.rng = rng or random.Random()
        self.entries = {e.id: e for e in entries if e.verified and (e.owner_confirmed or not require_owner_confirmed)}
        self.enabled, self.time_hm, self.mode, self.paused = False, (17, 0), "voice", False
        self.topics: set[str] = set()                         # empty = all
        self.sensitive_ok = False
        self.seen: dict[str, str] = {}                        # entry id -> day shown
        self.skipped: set[str] = set()
        self.saved: list[str] = []
        self.cat_last: dict[str, str] = {}
        self.last_scheduled_day: str | None = None
        self.offer: dict | None = None                        # the insight she can still ask about
        self.inbox: list[dict] = []
        self._load()

    # ---- persistence -----------------------------------------------------------------------------
    def _load(self) -> None:
        try:
            d = json.loads(self.path.read_text())
            if d.get("schema_version") != SCHEMA_VERSION:
                raise ValueError
            s = d["settings"]
            self.enabled, self.paused = bool(s["enabled"]), bool(s["paused"])
            h, m = s["time"]
            self.time_hm = (int(h) % 24, int(m) % 60)
            self.mode = s["mode"] if s["mode"] in MODES else "voice"
            self.topics = {t for t in s["topics"] if t in CATEGORIES}
            self.sensitive_ok = bool(s["sensitive_ok"])
            self.seen = {str(k): str(v) for k, v in d["seen"].items()}
            self.skipped = {str(x) for x in d["skipped"]}
            self.saved = [str(x) for x in d["saved"] if str(x) in self.entries]
            self.cat_last = {k: str(v) for k, v in d["cat_last"].items() if k in CATEGORIES}
            self.last_scheduled_day = d.get("last_scheduled_day")
            self.inbox = [i for i in d.get("inbox", []) if isinstance(i, dict) and i.get("id") in self.entries][-INBOX_MAX:]
        except FileNotFoundError:
            pass
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            self.enabled = False                              # damaged file: stay off rather than guess

    def _save(self) -> None:
        d = {"schema_version": SCHEMA_VERSION,
             "settings": {"enabled": self.enabled, "paused": self.paused, "time": list(self.time_hm), "mode": self.mode,
                          "topics": sorted(self.topics), "sensitive_ok": self.sensitive_ok},
             "seen": self.seen, "skipped": sorted(self.skipped), "saved": self.saved, "cat_last": self.cat_last,
             "last_scheduled_day": self.last_scheduled_day, "inbox": self.inbox[-INBOX_MAX:]}
        tmp = self.path.with_suffix(".tmp")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(d, f)
        os.replace(tmp, self.path)

    def _day(self, now: float) -> str:
        return datetime.fromtimestamp(now, self.tz).strftime("%Y-%m-%d")

    # ---- choosing --------------------------------------------------------------------------------
    def _eligible(self) -> list[Entry]:
        return [e for e in self.entries.values()
                if (not self.topics or e.topic in self.topics) and (self.sensitive_ok or not e.sensitive_topic)]

    def pick(self, topic: str | None = None) -> Entry | None:
        """Balanced rotation: the category not used for the longest time, an entry not shown yet (skipped ones last)."""
        pool = self._eligible()
        if topic:
            pool = [e for e in pool if e.topic == topic]
        if not pool:
            return None
        fresh = [e for e in pool if e.id not in self.seen and e.id not in self.skipped]
        if not fresh:
            fresh = [e for e in pool if e.id not in self.seen]              # only skipped ones are left: they come last
        if not fresh:
            for e in pool:                                                   # everything has been shown once: another round
                self.seen.pop(e.id, None)
            fresh = pool
        cats = sorted({e.topic for e in fresh}, key=lambda c: (self.cat_last.get(c, ""), c))
        oldest = [c for c in cats if self.cat_last.get(c, "") == self.cat_last.get(cats[0], "")]
        cat = self.rng.choice(oldest)
        return sorted((e for e in fresh if e.topic == cat), key=lambda e: e.id)[0]

    # ---- scheduled delivery ----------------------------------------------------------------------
    def due(self, now: float) -> bool:
        if not self.enabled or self.paused or self.quiet():
            return False
        local = datetime.fromtimestamp(now, self.tz)
        start = local.replace(hour=self.time_hm[0], minute=self.time_hm[1], second=0, microsecond=0).timestamp()
        return start <= now < start + WINDOW_S and self.last_scheduled_day != self._day(now)

    def prepare(self, now: float) -> Script | None:
        if not self.due(now):
            return None
        e = self.pick()
        return Script(e, script_for(e), True) if e else None

    def commit(self, script: Script, now: float, spoken: bool) -> None:
        """Call once the insight really was shown or spoken (not merely chosen)."""
        e, day = script.entry, self._day(now)
        self.seen[e.id], self.cat_last[e.topic] = day, day
        if script.scheduled:
            self.last_scheduled_day = day
        self.offer = {"id": e.id, "t": now}
        if self.mode in ("text", "both") or not spoken:
            self.inbox = (self.inbox + [{"id": e.id, "day": day, "text": script.text}])[-INBOX_MAX:]
        self._save()

    # ---- her voice commands ----------------------------------------------------------------------
    def _recent(self, now: float) -> Entry | None:
        if self.offer and now - self.offer["t"] <= CONTEXT_S:
            return self.entries.get(self.offer["id"])
        return None

    def handle(self, utterance: str, now: float | None = None):
        """(text, style, interaction) for a request this feature owns, else None. Fully scripted."""
        now = self.clock() if now is None else now
        t = utterance.strip()
        if not t:
            return None
        if OFF.search(t):
            self.enabled = False
            self._save()
            return ("Okay, no more daily discoveries. You can turn them back on any time.", "calm", "none")
        if ON.search(t):
            self.enabled, self.paused = True, False
            self._save()
            return (DISCLOSURE, "calm", "none")
        if RESUME.search(t):
            self.paused = False
            self._save()
            return ("Okay, the daily discoveries are back.", "calm", "none")
        if PAUSE.search(t):
            self.paused = True
            self._save()
            return ("Okay, I'll keep quiet about those until you say resume.", "calm", "none")
        m = SET_TIME.search(t)
        if m:
            h, mi, ap = int(m.group("h")), int(m.group("m") or 0), (m.group("ap") or "").lower().replace(".", "")
            if ap == "pm" and h < 12:
                h += 12
            if ap == "am" and h == 12:
                h = 0
            if not (0 <= h <= 23 and 0 <= mi <= 59) or (not ap and h <= 12 and ":" not in t and h < 7):
                return ("What time would you like it? For example, say 6 pm.", "calm", "none")
            self.time_hm = (h, mi)
            self._save()
            return (f"Okay, I'll share the daily discovery around {datetime(2000, 1, 1, h, mi).strftime('%-I:%M %p').replace(':00', '')}.", "calm", "none")
        m = SET_MODE.search(t)
        if m and not GIVE.search(t):
            self.mode = m.group("mode").lower()
            self._save()
            return (f"Okay, discoveries will come as {self.mode}.", "calm", "none")
        if SENSITIVE_ON.search(t):
            self.sensitive_ok = True
            self._save()
            return ("Okay, I'll include discoveries about relationships and communication too.", "calm", "none")
        if SENSITIVE_OFF.search(t):
            self.sensitive_ok = False
            self._save()
            return ("Okay, I'll leave out the relationship ones.", "calm", "none")
        if ALL_TOPICS.search(t):
            self.topics = set()
            self._save()
            return ("Okay, any topic.", "calm", "none")
        m = ONLY_TOPIC.search(t)
        if m:
            cat = self._topic_for(m.group("t"))
            if cat:
                self.topics = {cat}
                self._save()
                return (f"Okay, I'll stick to {cat.replace('_', ' ')}.", "calm", "none")
            return None
        if LIST_SAVED.search(t):
            if not self.saved:
                return ("You haven't saved any discoveries yet. After one, say save that.", "calm", "none")
            titles = "; ".join(self.entries[i].title for i in self.saved[-5:])
            return (f"You saved: {titles}. Say revisit and the title to hear one again.", "calm", "none")
        rv = REVISIT.search(t)
        if rv and not self._recent(now):
            found = self._find_saved(rv.group("what"))
            if found:
                return self._show(found, now, "calm", scheduled=False)
        if GIVE.search(t):
            topic = self._topic_in(t)
            if topic is None and re.search(r"\b(about|on|regarding)\b", t, _I):
                return ("I don't have a verified discovery about that yet. Say 'another one' and I'll pick a different topic.",
                        "calm", "none")
            e = self.pick(topic)
            if e is None:
                return ("I don't have a verified discovery for that yet.", "calm", "none") if topic else \
                       ("I don't have any verified discoveries ready yet.", "calm", "none")
            return self._show(e, now, "normal", scheduled=False)
        cur = self._recent(now)
        if cur is None:
            return None
        if ANOTHER.match(t):
            e = self.pick()
            return self._show(e, now, "normal", scheduled=False) if e else ("That's all I have for now.", "calm", "none")
        if SKIP.match(t):
            self.skipped.add(cur.id)
            self.offer = None
            self._save()
            return ("No problem, skipped.", "calm", "none")
        if SAVE.search(t):
            if cur.id not in self.saved:
                self.saved.append(cur.id)
            self._save()
            return ("Saved. You can ask me what you've saved any time.", "calm", "none")
        if SOURCE.search(t):
            return (f"This comes from {cur.sources_text()} The link is saved with it in your discoveries list. "
                    "Want to hear what it can't tell us, or try a tiny exercise?", "calm", "explore_topic")
        if EXERCISE.search(t):
            return (f"Here's a tiny one. {cur.optional_exercise}", "calm", "explore_topic")
        if DEEPER.search(t):
            return (f"Fair to wonder how far it goes. {cur.limitations} It's worth looking at the original too, and making up your own mind.",
                    "calm", "explore_topic")
        return None

    def _show(self, e: Entry, now: float, style: str, scheduled: bool):
        s = Script(e, script_for(e), scheduled)
        self.commit(s, now, spoken=True)
        return (s.text, style, "none")

    def _topic_for(self, words: str) -> str | None:
        w = words.strip().lower()
        for cat, names in TOPIC_WORDS.items():
            if w in names or w == cat.replace("_", " "):
                return cat
        return None

    def _topic_in(self, t: str) -> str | None:
        low = t.lower()
        for cat, names in TOPIC_WORDS.items():
            if any(re.search(rf"\b{re.escape(n)}\b", low) for n in names):
                return cat
        return None

    def _find_saved(self, text: str) -> Entry | None:
        words = set(re.findall(r"[a-z]{3,}", text.lower())) - {"the", "that", "one", "insight", "discovery", "about", "again"}
        best, score = None, 0
        for i in self.saved:
            e = self.entries[i]
            s = len(words & set(re.findall(r"[a-z]{3,}", e.title.lower())))
            if s > score:
                best, score = e, s
        return best
