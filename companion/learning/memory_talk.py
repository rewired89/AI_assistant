"""She can ask the robot what it remembers and tell it to forget, by voice (spec section 13, acceptance criterion 9).
Fully scripted: no language model sees these requests or writes the answers, and nothing here touches the private
reflection vault (it is not listed, searched or cleared here).

    "what do you remember about me?"      -> a few of the saved things, and how many there are
    "what routines have you noticed?"     -> the routines (confirmed, and the ones still waiting for her yes)
    "forget that" (right after a save)    -> asks, then deletes that one memory
    "forget my favorite color"            -> finds it, asks, deletes
    "forget everything"                   -> asks TWICE, then clears the ordinary memories
    "stop noticing my routines"           -> turns routine learning off and erases its evidence
Deleting always asks first. "don't forget to ..." is never a deletion request."""
from __future__ import annotations

import re
import time

from ..memory import MemoryError_
from ..memory.store import ORDINARY, _tokens

YES = {"yes", "yeah", "yep", "sure", "ok", "okay", "please", "do", "confirm", "right", "yup"}
NO = {"no", "nope", "don't", "dont", "not", "cancel", "stop", "never", "wait"}

_I = re.I
LIST_ALL = re.compile(r"\b(what|which) (do|did|have) you (remember|know|saved?|learned?|remembered) ?(about me)?\b|\bwhat('?s| is) in your memory\b|\bwhat have you (saved|kept|remembered)\b", _I)
LIST_ROUTINES = re.compile(r"\b(what|which) (routines?|habits?|patterns?) (have|did|do) you (noticed?|learned?|seen|know|remember)\b|\bwhat (routines?|habits?) do you (know|remember|have)\b", _I)
FORGET_ALL = re.compile(r"\b(forget|delete|erase|clear|wipe) (everything|all of it|all my (data|memories)|your memory)\b|\bforget (me|everything about me)\b|\bclear (all )?(your |my )?memor(y|ies)\b", _I)
FORGET_ROUTINES = re.compile(r"\b(forget|delete|erase|clear|remove) (all )?(my |the |your )?(routines?|habits?)\b", _I)
STOP_ROUTINES = re.compile(r"\b(stop|quit|don'?t|do not) (noticing|tracking|learning|watching|keeping track of) (my |any |the )?(routines?|habits?|patterns?)\b|\bturn off (the )?(routine|habit)", _I)
START_ROUTINES = re.compile(r"\b(start|resume|begin|go back to|you can) (noticing|tracking|learning) (my |the )?(routines?|habits?)( again)?\b", _I)
FORGET_THAT = re.compile(r"^\W*(please )?(forget|delete|remove|erase) (that|it|this)\W*$", _I)
FORGET_TOPIC = re.compile(r"^\W*(please )?(forget|delete|remove|erase) (?:about |that i |that |the fact that |my |the )?(?P<topic>[^.!?]{2,80})", _I)
HOW_CHANGED = re.compile(r"\bhow (have|did) you (changed?|grown|been changing)\b|\bhave you (changed|been changing)\b", _I)
RESET_SELF = re.compile(r"\b(reset|undo) (your |the )?(personality|changes|how you act)\b|\bgo back to (how|the way) you (were|started|used to be)\b", _I)
STOP_ADAPT = re.compile(r"\b(stop|quit|don'?t|do not) (changing|adapting|adjusting)( (your|the) (personality|way you act))?\b|\bturn off (the )?(adapting|personality changes)\b", _I)
START_ADAPT = re.compile(r"\b(start|resume|begin|you can) (changing|adapting)( again)?\b", _I)
NEGATED = re.compile(r"\b(don'?t|do not|never|not to|didn'?t|can'?t|cannot|won'?t) (ever )?(forget|delete)\b|\bforget (to|about it later)\b", _I)

ASK_CLEAR = "That would delete everything I remember about you. Are you sure?"
ASK_CLEAR_2 = "Really everything? Say yes once more and I will forget it all."


def _say(rec: dict) -> str:
    v = re.sub(r"\s+", " ", str(rec["value"]))[:100]
    key = rec.get("key")
    return f"your {key.replace('_', ' ')} is {v}" if key and not key.startswith("routine_") else v


class MemoryTalk:
    CONFIRM_WINDOW_S = 90.0                                  # a "yes" only counts shortly after the question

    def __init__(self, memory, learner=None, max_spoken: int = 4, clock=time.time, adapter=None):
        self.memory, self.learner, self.max_spoken, self.clock = memory, learner, max_spoken, clock
        self.adapter = adapter                                # PersonalityAdapter (optional)
        self._n = 0
        self._pending: dict | None = None
        self._last_saved: tuple[str, int] | None = None

    def cancel(self) -> None:
        """Anything else taking over the conversation (a safety reply, a private exercise) drops a waiting question."""
        self._pending = None

    def _ask(self, **kw) -> None:
        self._pending = {"turn": self._n, "t": self.clock(), **kw}

    def note_saved(self, memory_id: str) -> None:
        self._last_saved = (memory_id, self._n)

    # ---- helpers ------------------------------------------------------------------------------
    def _ordinary(self, include_pending: bool = False) -> list[dict]:
        out = []
        for c in ORDINARY:
            try:
                out += self.memory.list(c, include_pending=include_pending)
            except MemoryError_:
                pass
        return sorted(out, key=lambda r: r["updated_at"], reverse=True)

    def _pending_count(self) -> int:
        return len([r for r in self._ordinary(True) if r["status"] == "pending"])

    def _match(self, topic: str) -> list[dict]:
        q = set(_tokens(topic))
        if not q:
            return []
        scored = []
        for r in self._ordinary(True):
            doc = set(_tokens(f"{r.get('key') or ''} {r['value']}"))
            hit = len(q & doc)
            if hit:
                scored.append((hit / len(q), r))
        scored.sort(key=lambda t: -t[0])
        if not scored or scored[0][0] < 0.5:
            return []
        top = scored[0][0]
        return [r for s, r in scored if s >= top - 1e-9][:3]

    # ---- the one entry point ---------------------------------------------------------------------
    def handle(self, utterance: str):
        """Returns (text, style) when this was a memory request, else None (the normal conversation goes on)."""
        from ..speech.pipeline import Reply
        self._n += 1
        pending, self._pending = self._pending, None
        words = set(re.findall(r"[a-z']+", utterance.lower()))
        if pending and pending["turn"] == self._n - 1 and self.clock() - pending["t"] <= self.CONFIRM_WINDOW_S:
            yes, no = bool(words & YES), bool(words & NO)
            if yes and not no:
                return self._do(pending, Reply)
            if no and not yes:
                return Reply("Okay, I won't forget it.", "calm", interaction="none")
        if NEGATED.search(utterance):
            return None
        t = utterance.strip()
        if self.adapter is not None:
            if HOW_CHANGED.search(t):
                return Reply(self.adapter.describe(), "calm", interaction="none")
            if RESET_SELF.search(t):
                self._ask(action="reset_self")
                return Reply("Do you want me to go back to exactly how I started?", "calm", interaction="none")
            if STOP_ADAPT.search(t):
                self.adapter.set_enabled(False)
                return Reply("Okay, I'll stay just as I am and I've undone any changes.", "calm", interaction="none")
            if START_ADAPT.search(t):
                self.adapter.set_enabled(True)
                return Reply("Okay, I'll change a little as we spend time together. You can ask how, or tell me to stop.", "calm", interaction="none")
        if LIST_ROUTINES.search(t):
            return self._list_routines(Reply)
        if LIST_ALL.search(t):
            return self._list_all(Reply)
        if STOP_ROUTINES.search(t):
            if self.learner is None:
                return Reply("I'm not keeping track of routines.", "calm", interaction="none")
            self.learner.set_enabled(False)
            return Reply("Okay, I've stopped noticing your routines and erased what I'd collected.", "calm", interaction="none")
        if START_ROUTINES.search(t):
            if self.learner is None:
                return None
            self.learner.set_enabled(True)
            return Reply("Okay, I'll start noticing routines again. I'll always ask before I remember one.", "calm", interaction="none")
        if FORGET_ROUTINES.search(t):
            self._ask(action="routines")
            return Reply("Do you want me to forget the routines I've noticed?", "calm", interaction="none")
        if FORGET_ALL.search(t):
            self._ask(action="all1")
            return Reply(ASK_CLEAR, "calm", interaction="none")
        if FORGET_THAT.match(t):
            if self._last_saved and self._n - self._last_saved[1] <= 2:
                rec = self._get(self._last_saved[0])
                if rec:
                    self._ask(action="one", ids=[rec["id"]])
                    return Reply(f"Do you want me to forget that {_say(rec)}?", "calm", interaction="none")
            return None                                       # "forget it" with nothing just saved is ordinary speech
        m = FORGET_TOPIC.match(t)
        if m:
            found = self._match(m.group("topic"))
            if not found:
                return Reply("I don't have anything saved about that.", "calm", interaction="none")
            if len(found) == 1:
                self._ask(action="one", ids=[found[0]["id"]])
                return Reply(f"Do you want me to forget that {_say(found[0])}?", "calm", interaction="none")
            listing = "; ".join(_say(r) for r in found)
            return Reply(f"I'm not sure which one you mean. I have: {listing}. Tell me which one to forget.", "calm", interaction="none")
        return None

    def _get(self, mid: str):
        for r in self._ordinary(True):
            if r["id"] == mid:
                return r
        return None

    def _do(self, p: dict, Reply):
        act = p["action"]
        if act == "one":
            for mid in p["ids"]:
                try:
                    self.memory.delete(mid)
                except MemoryError_:
                    pass
            return Reply("Okay, I've forgotten it.", "calm", interaction="none")
        if act == "reset_self" and self.adapter is not None:
            self.adapter.reset()
            return Reply("Okay, I'm back to how I started.", "calm", interaction="none")
        if act == "routines":
            n = self.learner.forget_routines() if self.learner else 0
            return Reply("Okay, I've forgotten the routines I'd noticed." if n else "I hadn't noticed any routines yet.", "calm", interaction="none")
        if act == "all1":
            self._ask(action="all2")
            return Reply(ASK_CLEAR_2, "calm", interaction="none")
        if act == "all2":
            total = 0
            for c in ORDINARY:
                try:
                    total += self.memory.clear(c)
                except MemoryError_:
                    pass
            if self.learner:
                self.learner.forget_evidence()
            return Reply("Okay. I've forgotten everything I remembered about you.", "calm", interaction="none")
        return None

    def _list_all(self, Reply):
        recs = self._ordinary(False)
        if not recs:
            return Reply("I haven't saved anything about you yet.", "calm", interaction="none")
        shown = recs[:self.max_spoken]
        text = f"I remember {len(recs)} thing{'s' if len(recs) != 1 else ''}. " + ". ".join(_say(r)[:1].upper() + _say(r)[1:] for r in shown) + "."
        if len(recs) > len(shown):
            text += f" And {len(recs) - len(shown)} more."
        pend = self._pending_count()
        if pend:
            text += f" I'm also not sure about {pend} thing{'s' if pend != 1 else ''}, so I haven't kept {'them' if pend != 1 else 'it'}."
        return Reply(text + " You can tell me to forget any of it.", "normal", interaction="none")

    def _list_routines(self, Reply):
        rs = self.learner.routines() if self.learner else []
        if not rs:
            return Reply("I haven't noticed any routines yet.", "calm", interaction="none")
        sure = [r["value"] for r in rs if r["status"] == "active"]
        unsure = [r["value"] for r in rs if r["status"] == "pending"]
        parts = []
        if sure:
            parts.append("I remember: " + " ".join(sure))
        if unsure:
            parts.append("I think I noticed: " + " ".join(unsure) + " I'll ask before I keep it.")
        return Reply(" ".join(parts), "normal", interaction="none")
