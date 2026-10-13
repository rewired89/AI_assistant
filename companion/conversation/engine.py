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
from ..behavior.engine import Intent
from .dialogue import LinePicker, STAGE_WORDS, load_templates
from .prompt import build_system_prompt
from .safety import speechify, violations
from ..textnorm import normalize
from .tools import (SLEEP_WORDS, TOOL_SPECS, Remember, RequestSleep, ShowEmotion, parse_tool_calls)

SAFE_FALLBACK = "I'm not sure how to put that. Could you ask me another way?"
ABSENCE = re.compile(r"\b(haven'?t seen you|been (away|gone)|long time|missed you|miss you|where (were|have) you|"
                     r"for (hours|days|ages|so long)|welcome back|you('re| are) back|finally)\b", re.I)
ASSERTS_ABOUT_HER = re.compile(r"\b(i know (that )?you|you (were|went|did|had|have been|'ve been|just|already|must have|probably)|"
                               r"you('re| are) (always|never))\b", re.I)
PLAY = re.compile(r"\b(let'?s play|wanna play|want to play|play (a |some )?(game|games|something)|play with me|games?|hide and seek|riddles?)\b", re.I)
TEACH = re.compile(r"\b(did you know|let me teach|i'?ll teach|i want to teach|remember that|learn (that|about))\b", re.I)
EXPLORE = re.compile(r"\b(tell me about|what is|what are|how does|how do|why do|why is)\b", re.I)
PURPOSE = {
    "greet": "greet her warmly (your bond with her is {relationship})",
    "greet_after_battery_saving": "say you are awake again after a nap to save battery",
    "curious_question": "ask ONE curious open question about her interests; do not assume anything about her day",
    "play_invite": "invite her to play a simple game or do something silly together",
    "share_interest": "bring up this thing she told you, in one friendly sentence or question",
    "offer_save_battery": "say your battery is getting low and offer to take a nap to save power; sound calm, not distressed",
    "milestone": "happily say you have grown into a {stage_word}",
    "comfort_touch_reply": "react happily to being gently touched",
}
SCRIPTED_ONLY = {"followup_due"}          # never model-written: must stay content-free
QUESTION_KINDS = {"curious_question", "play_invite", "offer_save_battery", "share_interest"}
MAX_PROACTIVE_WORDS = 30
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
                 on_sensitive: Callable[[dict], None] | None = None, dialogue: LinePicker | None = None,
                 safety=None, psychology=None, learner=None, memory_talk=None, insights=None, checkin=None, notes_talk=None, remind_talk=None):
        self.llm, self.cfg_fn, self.state, self.clock, self.memory = llm, personality_cfg, state, clock, memory
        self.user_name, self.cfg, self.tz, self.on_sensitive = user_name, config or EngineConfig(), tz, on_sensitive
        self.dialogue = dialogue or LinePicker(load_templates(None))
        self.safety, self.psychology = safety, psychology      # SafetyGate / PsychologyEngine (optional)
        self.learner, self.memory_talk = learner, memory_talk  # RoutineLearner / MemoryTalk (optional)
        self.remind_talk = remind_talk                          # RemindTalk (optional): reminders by voice, scripted
        self.notes_talk = notes_talk                            # NotesTalk (optional): voice notes and lists, scripted
        self.checkin = checkin                                  # CheckIn (optional): scripted daily check-in, answers never stored
        self.insights = insights                                # DailyInsights (optional): scripted, verified content only
        self.canary = "tok-" + uuid.uuid4().hex
        self.history: deque = deque(maxlen=self.cfg.history_turns * 2)    # RAM only, never persisted
        self._confirm: dict | None = None
        self._turn = 0
        self._last_ask_turn = -999
        self.stats = {"blocked_replies": 0, "rejected_tool_calls": 0, "ungrounded": 0, "proactive_llm": 0,
                      "proactive_fallback": 0}

    def reset_history(self) -> None:
        self.history.clear()
        self._confirm = None

    # ---- entry point ----------------------------------------------------------------
    def respond(self, utterance: str) -> Reply:
        self._turn += 1
        if self.safety is not None:                            # always first, whatever mode we are in: fixed text, no model, nothing kept
            scripted = self.safety.check(utterance)
            if scripted:
                self._confirm = None
                if self.memory_talk is not None:
                    self.memory_talk.cancel()
                if self.checkin is not None:
                    self.checkin.cancel()
                if self.notes_talk is not None:
                    self.notes_talk.cancel()
                if self.remind_talk is not None:
                    self.remind_talk.cancel()
                return Reply(scripted, "calm", interaction="none")
        if self.notes_talk is not None and (self.notes_talk.dictating or self.psychology is None or self.psychology.sess is None):
            nt = self.notes_talk.handle(utterance)          # notes and lists: scripted, nothing here reaches the model or memory
            if nt is not None:
                self._confirm = None
                return Reply(nt.text, nt.style, interaction="none")
        if self.remind_talk is not None and (self.psychology is None or self.psychology.sess is None) and not (self.notes_talk and self.notes_talk.dictating):
            rt = self.remind_talk.handle(utterance)         # reminders: scripted; "set" is said only after the store accepted it
            if rt is not None:
                self._confirm = None
                return Reply(rt.text, rt.style, interaction="none")
        if self.checkin is not None:                           # after safety, before everything else: one coherent check-in flow
            busy = self.psychology is not None and self.psychology.sess is not None
            ci = self.checkin.handle(utterance, psychology_busy=busy)
            if ci is not None:
                self._confirm = None
                return Reply(ci.text, ci.style, interaction="none", paced=ci.paced, pauses=ci.pauses)
        if self.psychology is not None:
            pr = self.psychology.handle(utterance, screened=True)
            if pr is not None:                                  # private: not added to history, memory or growth
                if self.memory_talk is not None:
                    self.memory_talk.cancel()
                if self.checkin is not None:
                    self.checkin.cancel()
                return Reply(pr.text, pr.style, interaction="none", paced=True, pauses=tuple(pr.pauses))
        if self.memory_talk is not None:                        # "what do you remember?" / "forget that": scripted, no model
            asked = self.memory_talk.handle(utterance)
            if asked is not None:
                self._confirm = None
                return asked
        if self.insights is not None:                           # daily psychology discoveries: scripted from the verified library
            got = self.insights.handle(utterance)
            if got is not None:
                self._confirm = None
                text, style, interaction = got
                return Reply(text, style, interaction=interaction)
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
        reply.interaction = self._classify(utterance, actions)
        self._learn(utterance, reply)
        self.history.append({"role": "user", "content": utterance[:300]})
        self.history.append({"role": "assistant", "content": reply.text[:300]})
        return reply

    def _learn(self, utterance: str, reply: Reply) -> None:
        """Routine recognition: note this chat (time of day only), and at most occasionally ask whether to keep a routine
        that enough days of evidence support. Nothing is kept without her yes."""
        if self.learner is None:
            return
        try:
            self.learner.record_chat(utterance, self.clock())
            q = self.learner.next_question(self.clock())
        except Exception:
            return
        if q and self._confirm is None and reply.interaction != "none" and not reply.after_speech:
            reply.text = f"{reply.text} {q['question']}"
            self._confirm, self._last_ask_turn = {"id": q["id"], "turn": self._turn, "routine_key": q["key"]}, self._turn

    @staticmethod
    def _classify(utterance: str, actions: list) -> str:
        """Rough growth category from her words. Mistakes are cheap: growth has diminishing returns and daily caps."""
        if PLAY.search(utterance):
            return "play"
        if TEACH.search(utterance) or any(isinstance(a, Remember) and a.category == "semantic" for a in actions):
            return "teach"
        if EXPLORE.search(utterance):
            return "explore_topic"
        return "conversation"

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
            if not raw.strip() or (raw.lstrip().startswith("{") and '"name"' in raw):
                # small models often answer with a tool call only; ask again for the spoken words, no tools
                try:
                    again = self.llm.complete(system + "\nReply to the user now in one or two short spoken sentences.",
                                              messages, [])
                    raw2 = again.get("text", "")
                    if isinstance(raw2, str) and raw2.strip():
                        raw = raw2
                except LLMUnavailable:
                    raise
                except Exception as e:
                    raise LLMUnavailable(type(e).__name__) from None
            text = speechify(raw, self.cfg.reply_max_chars)
            if not violations(raw, self.canary) and not violations(text, self.canary) and text:
                return text, calls
            self.stats["blocked_replies"] += 1
            hook = getattr(self, "on_blocked", None)        # DEV RIGS ONLY: why a reply was dropped
            if hook is not None:
                try:
                    hook(raw, violations(raw, self.canary) or violations(text, self.canary) or ["empty"])
                except Exception:
                    pass
        return SAFE_FALLBACK, []                          # tool calls from a rule-breaking reply are discarded too

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
            mid = self.memory.add(rec)
        except (Disabled, MemoryError_):
            return None
        if self.memory_talk is not None:
            self.memory_talk.note_saved(mid)
        return mid

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
                if pend.get("routine_key") and self.learner is not None:
                    self.learner.decline(pend["routine_key"])      # do not ask again for a while
                return Reply("Okay, I won't remember that.", "calm")
        except MemoryError_:
            pass
        return None                                         # unrelated answer: it stays pending, conversation continues

    # ---- sleep ------------------------------------------------------------------------------
    def _sleep(self, act: RequestSleep, utterance: str, now: datetime, reply: Reply) -> None:
        if not SLEEP_WORDS.search(normalize(utterance)):               # the user's own words must ask for it
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

    # ---- proactive speech -----------------------------------------------------------------------
    def compose_intent(self, intent: Intent, relationship: str | None = None) -> Reply | None:
        """Words for a behavior Intent. Never raises: if the model is down or breaks a rule the scripted line
        is used. Returns None when the intent can no longer be spoken (its memory was deleted or disabled).
        The model sees only the facts in intent.grounding; no tools are offered; absence talk is rejected."""
        rel = relationship or self.state.relationship_state
        records = self._resolve(intent)
        if records is None:
            return None
        facts = [speechify(f"{(r.get('key') or 'note').replace('_', ' ')}: {r['value']}", 200) for r in records]
        fields = {"name": self.user_name() or "friend", "fact": speechify(records[0]["value"], 80) if records else "",
                  "stage_word": STAGE_WORDS.get(intent.data.get("stage", ""), "bigger fox")}
        text = None if intent.kind in SCRIPTED_ONLY else self._proactive_llm(intent, rel, facts, fields)
        if text is None:
            self.stats["proactive_fallback"] += 1
            text = self.dialogue.pick(intent.kind, rel).format(**fields)
        else:
            self.stats["proactive_llm"] += 1
        text = speechify(text, 300)
        self.history.append({"role": "assistant", "content": text[:300]})
        tone = intent.tone if intent.tone in ("normal", "happy", "excited", "sleepy", "sad", "surprised",
                                              "affectionate", "calm", "whisper") else "normal"
        return Reply(text, tone)

    def _resolve(self, intent: Intent) -> list[dict] | None:
        """Memory records an intent is grounded in, or None if any is gone (deleted, disabled, expired)."""
        out = []
        for g in intent.grounding:
            if g.startswith("memory:"):
                rec = self.memory.get(g[7:]) if self.memory is not None else None
                if rec is None:
                    return None
                out.append(rec)
        return None if intent.kind == "share_interest" and not out else out

    def _proactive_llm(self, intent: Intent, rel: str, facts: list[str], fields: dict) -> str | None:
        purpose = PURPOSE[intent.kind].format(relationship=rel, stage_word=fields["stage_word"])
        cfg = self.cfg_fn()
        now = datetime.fromtimestamp(self.clock(), self.tz)
        system = build_system_prompt(cfg, self.state.mood, rel, self.state.stage, self.user_name(), [], self.canary,
                                     now.strftime("%A %H:%M"))
        task = ("\nTask: you speak first now. Say ONE short spoken line (at most 25 words) to " + purpose + "."
                + (" Facts you may rely on: " + "; ".join(f'"{f}"' for f in facts) + "." if facts else
                   " You have no facts to rely on.")
                + " Do not state anything else as fact: nothing about what she did, where she was, how long she has been"
                  " away, the weather or events. Never mention her absence. " + (
                    "End with one question." if intent.kind in QUESTION_KINDS else "Do not ask a question."))
        try:
            out = self.llm.complete(system + task, [*self.history, {"role": "user", "content": "(it is your turn to speak first)"}], None)
            raw = out.get("text", "")
            if not isinstance(raw, str):
                return None
        except Exception:
            return None
        text = speechify(raw, 300)
        words = len(text.split())
        ok = (text and words <= MAX_PROACTIVE_WORDS and not violations(raw, self.canary) and not ABSENCE.search(text)
              and not ASSERTS_ABOUT_HER.search(text)
              and text.count("?") <= 1)
        if ok and intent.kind in QUESTION_KINDS and "?" not in text:
            ok = False
        if ok and intent.kind not in QUESTION_KINDS and "?" in text:
            ok = False
        if ok and intent.kind == "share_interest":   # must actually be about the remembered thing
            ok = bool(set(_tokens(fields["fact"])) & set(_tokens(text)))
        return text if ok else None
