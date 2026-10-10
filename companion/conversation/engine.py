"""ConversationEngine: turns an utterance into a Reply (text + voice style + validated follow-up actions).

utterance -> confirmation answers -> memory retrieval -> prompt -> LLM -> output safety check (retry once) ->
speechify -> tool calls as validated requests -> memory decisions -> history (RAM only).
The LLM never touches devices, files or config. Anything it asks for passes through host code that
validates, grounds it in the user's own words, and may refuse. If the model is down, respond() raises
and the voice pipeline speaks a scripted line.
"""
from __future__ import annotations

import re
import uuid
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone, tzinfo
from typing import Callable

from protocol import ProtocolError, validate_payload, build

from ..behavior.engine import MOOD_VOICE
from ..interfaces import LanguageModel
from ..memory import Disabled, MemoryError_, decide
from ..memory.extraction import SENSITIVE, extract_candidates
from ..memory.store import _tokens
from ..scheduling.sleep import AmbiguousTime, ClockError, build_sleep_payload, resolve_wake
from ..speech.pipeline import Reply
from ..tamagotchi.state import CharacterState
from .prompt import build_system_prompt
from .safety import speechify, violations
from .tools import (SLEEP_WORDS, TOOL_SPECS, Remember, RequestSleep, ShowEmotion, parse_tool_calls)

SAFE_FALLBACK = "I'm not sure how to put that. Could you ask me another way?"
YES = {"yes", "yeah", "yep", "sure", "please", "okay", "ok", "correct", "right"}
NO = {"no", "nope", "don't", "dont", "never"}


class LLMUnavailable(Exception):
    """The language model could not be reached or returned something unusable."""


@dataclass
class EngineConfig:
    history_turns: int = 6
    memory_hits: int = 5
    max_tool_calls: int = 3
    reply_max_chars: int = 600
    confirm_cooldown_turns: int = 3
    grounding_threshold: float = 0.7


class ConversationEngine:
    def __init__(self, llm: LanguageModel, personality_cfg: Callable[[], dict], state: CharacterState,
                 clock: Callable[[], float], memory=None, user_name: Callable[[], str | None] = lambda: None,
                 config: EngineConfig | None = None, tz: tzinfo = timezone.utc,
                 on_sensitive: Callable[[dict], None] | None = None):
        self.llm, self.cfg_fn, self.state, self.clock, self.memory = llm, personality_cfg, state, clock, memory
        self.user_name, self.cfg, self.tz, self.on_sensitive = user_name, config or EngineConfig(), tz, on_sensitive
        self.canary = "tok-" + uuid.uuid4().hex
        self.history: deque = deque(maxlen=self.cfg.history_turns * 2)    # RAM only, never persisted
        self._confirm: dict | None = None
        self._turn = 0
        self._last_ask_turn = -999
        self.stats = {"blocked_replies": 0, "rejected_tool_calls": 0, "ungrounded": 0}

    def reset_history(self) -> None:
        self.history.clear()
        self._confirm = None

    # ---- entry point ----------------------------------------------------------------
    def respond(self, utterance: str) -> Reply:
        self._turn += 1
        answered = self._answer_confirmation(utterance)
        if answered:
            return answered
        hits = self._retrieve(utterance)
        cfg = self.cfg_fn()
        now = datetime.fromtimestamp(self.clock(), self.tz)
        system = build_system_prompt(cfg, self.state.mood, self.state.relationship_state, self.state.stage,
                                     self.user_name(), hits, self.canary, now.strftime("%A %H:%M"))
        messages = [*self.history, {"role": "user", "content": utterance}]
        text, calls = self._ask_model(system, messages)
        actions, rejected = parse_tool_calls(calls, self.cfg.max_tool_calls)
        self.stats["rejected_tool_calls"] += rejected
        reply = Reply(text, MOOD_VOICE.get(self.state.mood, "normal"))
        for act in actions:
            if isinstance(act, ShowEmotion):
                reply.animation = act.name
            elif isinstance(act, RequestSleep):
                self._sleep(act, utterance, now, reply)
            elif isinstance(act, Remember):
                self._remember(act, utterance, reply)
        for cand in extract_candidates(utterance):          # deterministic backstop for plain statements
            self._store_candidate(cand, utterance)
        self.history.append({"role": "user", "content": utterance[:300]})
        self.history.append({"role": "assistant", "content": reply.text[:300]})
        return reply

    # ---- model call with output validation ----------------------------------------------
    def _ask_model(self, system: str, messages: list[dict]) -> tuple[str, list]:
        for attempt in (0, 1):
            extra = "" if attempt == 0 else "\nYour previous reply broke the rules. Answer again, kindly and safely."
            try:
                out = self.llm.complete(system + extra, messages, TOOL_SPECS)
                raw, calls = out.get("text", ""), out.get("tool_calls") or []
                if not isinstance(raw, str):
                    raise TypeError("text")
            except LLMUnavailable:
                raise
            except Exception as e:
                raise LLMUnavailable(type(e).__name__) from None
            text = speechify(raw, self.cfg.reply_max_chars)
            if not violations(raw, self.canary) and not violations(text, self.canary) and text:
                return text, calls
            self.stats["blocked_replies"] += 1
        return SAFE_FALLBACK, []                            # tool calls from a rule-breaking reply are discarded too

    # ---- memory ---------------------------------------------------------------------------
    def _retrieve(self, utterance: str) -> list[dict]:
        if self.memory is None:
            return []
        try:
            return self.memory.search(utterance, limit=self.cfg.memory_hits)
        except MemoryError_:
            return []

    def _grounded(self, value: str, utterance: str) -> bool:
        v, u = set(_tokens(value)), set(_tokens(utterance))
        return bool(v) and len(v & u) / len(v) >= self.cfg.grounding_threshold

    def _remember(self, act: Remember, utterance: str, reply: Reply) -> None:
        grounded = self._grounded(act.value, utterance)
        cand = {"category": act.category, "key": act.key, "value": act.value,
                "source": "explicit_user_statement" if grounded else "ai_inference",
                "confidence": 1.0 if grounded else 0.5, "source_context": utterance[:200],
                "sensitive": bool(SENSITIVE.search(act.value) or SENSITIVE.search(utterance))}
        if not grounded:
            self.stats["ungrounded"] += 1
        mid = self._store_candidate(cand, utterance)
        if mid and not grounded and self._turn - self._last_ask_turn >= self.cfg.confirm_cooldown_turns:
            self._confirm, self._last_ask_turn = {"id": mid, "turn": self._turn}, self._turn
            reply.text = f"{reply.text} Do you want me to remember that {speechify(act.value, 120)}?"

    def _store_candidate(self, cand: dict, utterance: str) -> str | None:
        if self.memory is None:
            return None
        verdict = decide(cand)
        if verdict == "drop":
            return None
        if cand.get("sensitive"):                          # never into ordinary memory; the psychology layer owns consent
            if self.on_sensitive:
                self.on_sensitive({k: cand[k] for k in ("category", "value")})
            return None
        rec = {k: cand[k] for k in ("category", "key", "value", "source", "confidence", "source_context") if k in cand}
        try:
            return self.memory.add(rec)
        except (Disabled, MemoryError_):
            return None

    def _answer_confirmation(self, utterance: str) -> Reply | None:
        pend, self._confirm = self._confirm, None
        if not pend or self.memory is None or self._turn - pend["turn"] > 1:
            return None
        words = set(re.findall(r"[a-z']+", utterance.lower()))
        yes, no = bool(words & YES), bool(words & NO)
        try:
            if yes and not no:
                self.memory.confirm(pend["id"])
                return Reply("Got it, I'll remember that.", "happy")
            if no and not yes:
                self.memory.delete(pend["id"])
                return Reply("Okay, I won't remember that.", "calm")
        except MemoryError_:
            pass
        return None                                         # unrelated answer: it stays pending, conversation continues

    # ---- sleep ------------------------------------------------------------------------------
    def _sleep(self, act: RequestSleep, utterance: str, now: datetime, reply: Reply) -> None:
        if not SLEEP_WORDS.search(utterance):               # the user's own words must ask for it
            self.stats["rejected_tool_calls"] += 1
            return
        try:
            wake = None
            if act.hour is not None:
                wake = resolve_wake(act.hour, act.minute, act.meridiem, now)
            payload = build_sleep_payload(wake, now, reason=act.reason, requested_by="user")
            validate_payload(build("power.sleep", payload), "command")
        except AmbiguousTime as q:
            reply.text = str(q)                             # ask; never guess
            return
        except ClockError:
            reply.text = "I can't be sure what time it is, so I won't set a wake-up time."
            return
        except ProtocolError:
            self.stats["rejected_tool_calls"] += 1
            return
        reply.text = self._sleep_confirmation(wake, now)
        reply.after_speech = (("power.sleep", payload),)

    @staticmethod
    def _sleep_confirmation(wake: datetime | None, now: datetime) -> str:
        if wake is None:
            return "Okay, going to sleep now."
        h = wake.hour % 12 or 12
        clock = f"{h}:{wake.minute:02d} {'AM' if wake.hour < 12 else 'PM'}"
        day = "" if wake.date() == now.date() else "tomorrow at " if wake.date() == (now + timedelta(days=1)).date() \
            else wake.strftime("%A at ")
        return f"Okay, I'll nap until {day}{clock}."
