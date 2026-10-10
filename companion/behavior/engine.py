"""Behavior engine: (character state + personality + stimulus) -> typed Plan.

A Plan holds an animation request, a voice-style hint and dialogue *intents* (what to talk about,
never the words). Text comes later from templates or the LLM, constrained by each intent's
`grounding` and `constraints`. `to_commands` turns the animation into protocol commands, which the
device still validates. The engine never touches power: sleep and battery safety stay in firmware.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable

from protocol import build, validate_payload

from ..personality.config import Personality
from ..tamagotchi.state import CharacterState

PRIORITY = {"sleeping": 9, "waking": 8, "listening": 7, "thinking": 7, "talking": 7, "celebratory": 6,
            "reaction": 5, "idle": 2, "offline": 1}
MOOD_ANIMATION = {"sleeping": "sleeping", "sleepy": "sleepy", "playful": "playing", "curious": "curious",
                  "happy": "happy", "subdued": "calm", "calm": "calm"}
MOOD_VOICE = {"sleeping": "whisper", "sleepy": "sleepy", "playful": "excited", "curious": "normal",
              "happy": "happy", "subdued": "calm", "calm": "calm"}
# proactive_questions setting -> (min gap seconds, max per day, base probability per idle tick)
PROACTIVE = {"never": (0, 0, 0.0), "rare": (6 * 3600, 1, 0.15), "occasional": (3 * 3600, 3, 0.3),
             "frequent": (3600, 6, 0.5)}
INTENT_KINDS = {"greet", "greet_after_battery_saving", "curious_question", "play_invite", "share_interest",
                "offer_save_battery", "milestone", "comfort_touch_reply"}
NO_GUILT = {"mention_absence": False, "express_distress": False, "ask_for_attention": False}


@dataclass
class Intent:
    kind: str
    tone: str = "normal"
    grounding: list[str] = field(default_factory=list)  # facts the text may rely on; nothing else may be asserted
    constraints: dict = field(default_factory=lambda: dict(NO_GUILT))
    data: dict = field(default_factory=dict)


@dataclass
class Plan:
    animation: dict | None = None  # {"name","loop","priority"}
    voice_style: str = "normal"
    intents: list[Intent] = field(default_factory=list)
    stage_changed: str | None = None


@dataclass
class Settings:
    quiet_mode: bool = False
    quiet_hours: tuple[int, int] = (22, 8)  # local hours [start, end) with no proactive behavior
    idle_gap_s: int = 600                   # never proactive within this long of the last interaction


def to_commands(plan: Plan) -> list[tuple[str, dict]]:
    """Protocol-validated (type, payload) pairs. Raises ProtocolError if the plan is malformed."""
    out = []
    if plan.animation:
        msg = build("animation.play", dict(plan.animation))
        validate_payload(msg, "command")
        out.append((msg["type"], msg["payload"]))
    return out


class BehaviorEngine:
    def __init__(self, personality: Personality, state: CharacterState, clock: Callable[[], float],
                 rng: random.Random | None = None, settings: Settings | None = None):
        self.p, self.state, self.clock = personality, state, clock
        self.rng = rng or random.Random()
        self.settings = settings or Settings()
        self.last_proactive: float | None = None
        self._proactive_day, self._proactive_count = "", 0
        self._battery_offer_made = False
        self.host_online = True

    # ---- helpers --------------------------------------------------------
    def _anim(self, name: str, kind: str, loop: bool = False) -> dict:
        return {"name": name, "loop": loop, "priority": PRIORITY[kind]}

    def _idle(self) -> Plan:
        s = self.state
        s.advance(self.clock())
        name = MOOD_ANIMATION[s.mood]
        if s.mood in ("calm", "happy") and s.energy > 0.4:
            if self.rng.random() < 0.3 * self.p.value("playful"):
                name = "playing"
            elif self.rng.random() < 0.15 * self.p.value("mischievous"):
                name = "surprised"
        anim = self._anim("offline" if not self.host_online else name,
                          "offline" if not self.host_online else "idle", loop=True)
        return Plan(anim, MOOD_VOICE[s.mood])

    def _closeness(self) -> str:
        return self.state.relationship_state

    def _quiet_hour(self, now: float) -> bool:
        a, b = self.settings.quiet_hours
        h = datetime.fromtimestamp(now, self.state.tz).hour
        return (a <= h < b) if a < b else (h >= a or h < b)

    # ---- main entry -----------------------------------------------------
    def react(self, stimulus: str, **ctx) -> Plan:
        now = self.clock()
        s = self.state
        h = getattr(self, "_on_" + stimulus, None)
        if h is None:
            raise ValueError(f"unknown stimulus {stimulus!r}")
        s.advance(now)
        return h(now, **ctx)

    def _on_wake(self, now, cause="button", sleep_reason=None, **_):
        s = self.state
        s.set_sleeping(False, now)
        plan = Plan(self._anim("waking", "waking"), MOOD_VOICE[s.mood])
        if self.settings.quiet_mode:
            return plan
        if sleep_reason == "battery_saving" and cause == "scheduled":
            plan.intents.append(Intent("greet_after_battery_saving", tone=MOOD_VOICE[s.mood],
                                       grounding=["woke_from_battery_saving", "scheduled_wake"]))
        elif self.p.cfg["behavior"]["unsolicited_greetings"] or cause == "button":
            tone = "affectionate" if (self._closeness() != "new" and self.p.value("affectionate") > 0.6) \
                else MOOD_VOICE[s.mood]
            plan.intents.append(Intent("greet", tone=tone, grounding=[f"relationship_{self._closeness()}"]))
        return plan

    def _on_touch(self, now, **_):
        s = self.state
        if s.sleeping:
            return Plan()
        warm = self._closeness() != "new" and self.p.value("affectionate") > 0.5
        plan = Plan(self._anim("affectionate" if warm else "curious", "reaction"),
                    "affectionate" if warm else "normal")
        if not self.settings.quiet_mode:
            plan.intents.append(Intent("comfort_touch_reply", tone=plan.voice_style, grounding=["was_touched"]))
        return plan

    def _on_listening_start(self, now, **_):
        return Plan(self._anim("listening", "listening", loop=True))

    def _on_thinking(self, now, **_):
        return Plan(self._anim("thinking", "thinking", loop=True))

    def _on_speaking_start(self, now, style=None, **_):
        return Plan(self._anim("talking", "talking", loop=True), style or MOOD_VOICE[self.state.mood])

    def _on_speaking_end(self, now, **_):
        return self._idle()

    def _on_idle_tick(self, now, memory_refs=None, **_):
        p = self._proactive(now, memory_refs or [])
        plan = self._idle()
        if p:
            plan.intents.append(p)
            plan.voice_style = p.tone
        return plan

    def _on_host_disconnected(self, now, **_):
        self.host_online = False
        return Plan(self._anim("offline", "offline", loop=True))

    def _on_host_connected(self, now, **_):
        self.host_online = True
        return self._idle()

    def _on_battery_warning(self, now, **_):
        plan = self._idle()
        if not self._battery_offer_made and not self.settings.quiet_mode:
            self._battery_offer_made = True  # once per warning episode
            plan.intents.append(Intent("offer_save_battery", tone="calm", grounding=["battery_warning"]))
        return plan

    def _on_battery_ok(self, now, **_):
        self._battery_offer_made = False
        return self._idle()

    def _on_battery_low(self, now, **_):
        return Plan(self._anim("sleepy", "idle", loop=True), "sleepy")  # no chatter, no proactivity

    _on_battery_critical = _on_battery_low

    def _on_sleep_entering(self, now, **_):
        self.state.set_sleeping(True, now)
        return Plan(self._anim("sleeping", "sleeping", loop=True), "whisper")

    def _on_interaction(self, now, kind="conversation", **_):
        r = self.state.apply_event(kind, now)
        plan = self._idle()
        if r["stage_changed"]:
            plan = Plan(self._anim("celebratory", "celebratory"), "excited", stage_changed=r["stage_changed"])
            if not self.settings.quiet_mode:
                plan.intents.append(Intent("milestone", tone="excited", grounding=[f"stage_{r['stage_changed']}"],
                                           data={"stage": r["stage_changed"]}))
        return plan

    # ---- proactive ------------------------------------------------------
    def _proactive(self, now: float, memory_refs: list[str]) -> Intent | None:
        s, b = self.state, self.p.cfg["behavior"]
        gap, per_day, prob = PROACTIVE[b["proactive_questions"]]
        if per_day == 0 or self.settings.quiet_mode or s.sleeping or not self.host_online:
            return None
        if s.energy < 0.3 or self._quiet_hour(now):
            return None
        if s.last_interaction is not None and now - s.last_interaction < self.settings.idle_gap_s:
            return None
        if self.last_proactive is not None and now - self.last_proactive < gap:
            return None
        day = datetime.fromtimestamp(now, s.tz).strftime("%Y-%m-%d")
        if day != self._proactive_day:
            self._proactive_day, self._proactive_count = day, 0
        if self._proactive_count >= per_day:
            return None
        if self.rng.random() >= prob * (0.5 + self.p.value("curious")):
            return None
        intent = None
        if s.curiosity > 0.7:
            intent = Intent("curious_question", tone="normal", grounding=["curiosity_high"])
        elif s.playfulness > 0.7 and s.energy > 0.5:
            intent = Intent("play_invite", tone="excited", grounding=["playfulness_high"])
        elif memory_refs:
            intent = Intent("share_interest", tone=MOOD_VOICE[s.mood], grounding=[f"memory:{m}" for m in memory_refs[:3]])
        if intent:
            self.last_proactive, self._proactive_count = now, self._proactive_count + 1
        return intent
