"""Steps through one exercise. Pure logic; the engine owns consent, safety and storage.

She can skip any step, stop at any time, and nothing here judges an answer: there is no right answer and no score.
`indicators()` returns yes/no facts about what she did (e.g. named a feeling), used only when she chooses to save a note."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, tzinfo

from ...scheduling.sleep import AmbiguousTime, ClockError, resolve_wake

STOP = re.compile(r"^\W*(please\s+)?(can we |could we |let'?s |i want to |i'?d like to |i think we should )?"
                  r"(stop|quit|end( this| it)?|cancel|enough|that'?s enough|never ?mind|not now|i don'?t want to do this( anymore)?)"
                  r"( this| it| please| now)*\W*$", re.I)
SKIP = re.compile(r"^\s*(skip|pass|next|i'?d rather not|i don'?t know|no idea|not sure)\s*[.!]?\s*$", re.I)
YES = {"yes", "yeah", "yep", "sure", "ok", "okay", "please", "yup", "definitely", "alright"}
NO = {"no", "nope", "nah", "not"}
NUMBERS = {w: i for i, w in enumerate("zero one two three four five six seven eight nine ten".split())}
MAX_ANSWER = 500


@dataclass
class StepOut:
    text: str = ""
    done: bool = False
    stopped: bool = False
    suggest: str | None = None        # another exercise id to offer next (e.g. slow_breathing)
    pauses: list[float] = field(default_factory=list)
    followup_at: float | None = None  # epoch seconds she chose for a return time


def parse_scale(text: str) -> int | None:
    m = re.search(r"\b(\d{1,2})\b", text)
    n = int(m.group(1)) if m else next((v for w, v in NUMBERS.items() if re.search(rf"\b{w}\b", text.lower())), None)
    return n if n is not None and 0 <= n <= 10 else None


def parse_return_time(text: str, now: datetime) -> datetime:
    """'tonight at 8', 'in 30 minutes', 'tomorrow at 9 am', '8:30 pm'. Raises AmbiguousTime with a question."""
    t = text.lower()
    m = re.search(r"\bin (\d+|an?|one|two|three|four|five|ten|fifteen|twenty|thirty|forty five|sixty) (minute|min|hour|hr)s?\b", t)
    if m:
        words = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "ten": 10, "fifteen": 15,
                 "twenty": 20, "thirty": 30, "forty five": 45, "sixty": 60}
        n = int(m.group(1)) if m.group(1).isdigit() else words[m.group(1)]
        return now + (timedelta(hours=n) if m.group(2).startswith("h") else timedelta(minutes=n))
    if "noon" in t:
        return resolve_wake(12, 0, "pm", now)
    if "midnight" in t:
        return resolve_wake(12, 0, "am", now)
    m = re.search(r"\b(\d{1,2})(?::(\d{2}))?\s*(a\.?m\.?|p\.?m\.?)?", t)
    if not m:
        raise AmbiguousTime("What time exactly? For example, tonight at eight, or in thirty minutes.")
    hour, minute = int(m.group(1)), int(m.group(2) or 0)
    mer = (m.group(3) or "").replace(".", "") or None
    if mer is None:
        mer = "pm" if re.search(r"tonight|evening|afternoon|night", t) else "am" if "morning" in t else None
    wake = resolve_wake(hour, minute, mer, now)
    if "tomorrow" in t and wake.date() == now.date():
        wake = wake + timedelta(days=1)
    return wake


class ExerciseRunner:
    def __init__(self, exercise: dict, clock, tz: tzinfo):
        self.ex, self.clock, self.tz = exercise, clock, tz
        self.steps = exercise["steps"]
        self.i = 0
        self.answers: dict[str, str | None] = {}
        self.tries = 0
        self.stopped = self.done = False
        self.followup_at: float | None = None
        self.route_to: str | None = None

    # ---- driving --------------------------------------------------------------------------
    def start(self) -> StepOut:
        return self._advance(StepOut())

    def answer(self, text: str) -> StepOut:
        if self.done:
            return StepOut(done=True, stopped=self.stopped)
        text = " ".join(text.split())[:MAX_ANSWER]
        step = self.steps[self.i]
        if STOP.match(text):                                  # only a short command, never words inside an answer
            return self.stop()
        if SKIP.match(text):
            self.answers[step["id"]] = None
            return self._next(StepOut())
        handler = getattr(self, "_ans_" + step["kind"])
        return handler(step, text)

    def stop(self) -> StepOut:
        self.done = self.stopped = True
        return StepOut("Okay, we'll stop here. You did the right thing by listening to yourself.", done=True, stopped=True)

    # ---- per kind ---------------------------------------------------------------------------
    def _ans_ask_text(self, step, text):
        self.answers[step["id"]] = text
        return self._next(StepOut())

    def _ans_ask_choice(self, step, text):
        words = set(re.findall(r"[a-z']+", text.lower()))
        hit = next((o for o in step["options"] if set(str(o).lower().split()) <= words), None)
        if hit is None and step.get("allow_other") and text:
            hit = text
        if hit is None:
            return self._retry(step, "Sorry, which one: " + ", ".join(str(o) for o in step["options"]) + "?")
        self.answers[step["id"]] = str(hit)
        if step.get("route") and str(hit) in step["route"]:
            self.route_to = step["route"][str(hit)]
            self.done = True
            return StepOut(step.get("route_text", "Okay."), done=True, suggest=self.route_to)
        return self._next(StepOut())

    def _ans_ask_scale(self, step, text):
        n = parse_scale(text)
        if n is None:
            return self._retry(step, "Could you give me a number from zero to ten?")
        self.answers[step["id"]] = str(n)
        if step.get("high_threshold") is not None and n >= step["high_threshold"]:
            self.done = self.stopped = True
            return StepOut("That sounds really intense, so let's stop the questions here. Would you like to try slow breathing, "
                           "or take a break? You don't have to do anything else right now.", done=True, stopped=True,
                           suggest="slow_breathing")
        return self._next(StepOut())

    def _ans_ask_yesno(self, step, text):
        words = set(re.findall(r"[a-z']+", text.lower()))
        soft_yes = bool(re.search(r"\b(a little|a bit|somewhat|kind of|kinda|sort of|better|calmer|slightly)\b", text.lower()))
        yes, no = bool(words & YES) or soft_yes, bool(words & NO or re.search(r"\b(worse|nothing changed)\b", text.lower()))
        if yes == no:
            return self._retry(step, "Just a yes or a no is fine.")
        self.answers[step["id"]] = "yes" if yes else "no"
        if step["id"] == "ok" and no:                         # consent-style first step
            self.done = self.stopped = True
            return StepOut("No problem at all. We can skip it.", done=True, stopped=True)
        return self._next(StepOut())

    def _ans_ask_time(self, step, text):
        now = datetime.fromtimestamp(self.clock(), self.tz)
        try:
            when = parse_return_time(text, now)
            if when - now > timedelta(hours=72):
                raise AmbiguousTime("That's quite far off for coming back to this. Could you pick a sooner time?")
            if when - now < timedelta(minutes=5):
                raise AmbiguousTime("Give yourselves at least a few minutes. What time would work?")
        except AmbiguousTime as q:
            return self._retry(step, str(q))
        except ClockError:
            self.answers[step["id"]] = None
            return self._next(StepOut("I can't be sure what time it is, so I won't set a reminder. Choose the time yourselves."))
        self.followup_at = when.timestamp()
        h = when.hour % 12 or 12
        self.answers[step["id"]] = f"{h}:{when.minute:02d} {'AM' if when.hour < 12 else 'PM'}"
        out = StepOut(followup_at=self.followup_at)
        return self._next(out)

    # ---- plumbing ----------------------------------------------------------------------------
    def _retry(self, step, text: str) -> StepOut:
        self.tries += 1
        if self.tries >= 2:                                  # never loop on her
            self.answers[step["id"]] = None
            return self._next(StepOut("That's okay, we'll move on."))
        return StepOut(text)

    def _next(self, out: StepOut) -> StepOut:
        self.i += 1
        self.tries = 0
        return self._advance(out)

    def _advance(self, out: StepOut) -> StepOut:
        parts = [out.text] if out.text else []
        while self.i < len(self.steps):
            st = self.steps[self.i]
            when = st.get("when")
            if when and self.answers.get(when["step"]) not in when["in"]:
                self.i += 1
                continue
            if st["kind"] == "say":
                parts.append(st["text"])
                if st.get("wait_s"):
                    out.pauses.append(float(st["wait_s"]))
                self.i += 1
            elif st["kind"] == "build_text":
                vals = {k: (self.answers.get(k) or "...") for k in re.findall(r"{(\w+)}", st["template"])}
                self.answers[st["id"]] = st["template"].format(**vals)
                parts.append(st.get("lead", "") + self.answers[st["id"]])
                self.i += 1
            else:
                parts.append(st["prompt"])
                out.text = " ".join(p for p in parts if p)
                return out
        self.done = True
        out.done, out.followup_at = True, self.followup_at
        out.text = " ".join(p for p in parts if p)
        return out

    # ---- results (only ever used for a note she approves) ---------------------------------------
    def recap(self) -> list[tuple[str, str]]:
        return [(st["recap"], self.answers[st["id"]]) for st in self.steps
                if st.get("recap") and self.answers.get(st["id"])]

    def indicators(self) -> dict[str, bool]:
        return {st["indicator"]: bool(self.answers.get(st["id"])) for st in self.steps if st.get("indicator")}
