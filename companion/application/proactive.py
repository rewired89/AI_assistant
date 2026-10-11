"""ProactiveDirector: lets the creature speak first, within the behavior engine's limits.

Stimuli (device events, finished voice turns, idle time) -> BehaviorEngine plans -> Intents -> words from
ConversationEngine.compose_intent (model-written inside tight grounding, or scripted) -> VoicePipeline.speak.
Rules: speech only when the pipeline is idle and the device awake; stale intents expire instead of being said
late; composing runs off-thread so a slow model never freezes the loop; the microphone is never opened here;
memories are spoken only if they are active, not sensitive, and not used in the last 7 days.
Call tick() from the same loop that calls VoicePipeline.poll().
"""
from __future__ import annotations

import random
from collections import Counter, deque
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from typing import Callable

from ..behavior import BehaviorEngine, Intent, Plan, to_commands
from ..conversation.engine import ConversationEngine
from ..speech.pipeline import State, VoicePipeline

SKIP_KEYS = {"user_name", "preferred_name"}


@dataclass
class DirectorConfig:
    idle_tick_s: float = 60.0
    pending_ttl_s: float = 30.0
    memory_reuse_s: float = 7 * 86400.0
    save_interval_s: float = 300.0
    speak_memories: bool = True
    memory_candidates: int = 3
    max_memory_chars: int = 80


class ProactiveDirector:
    def __init__(self, behavior: BehaviorEngine, conversation: ConversationEngine, pipeline: VoicePipeline,
                 clock: Callable[[], float], memory=None, state_store=None, config: DirectorConfig | None = None,
                 rng: random.Random | None = None):
        self.behavior, self.conversation, self.pipeline, self.clock = behavior, conversation, pipeline, clock
        self.memory, self.state_store, self.cfg = memory, state_store, config or DirectorConfig()
        self.rng = rng or random.Random()
        self.stats: Counter = Counter()
        self.asleep = False
        self._sleep_reason: str | None = None
        self._stimuli: deque = deque()
        self._pending: deque = deque()                      # (expires, Intent, voice_style)
        self._composing: tuple[Future, Intent, str] | None = None
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="proactive")
        self._used: dict[str, float] = {}
        self._last_idle = self._last_save = clock()
        self._battery = "ok"
        self._last_anim: str | None = None
        pipeline.client.add_listener(self._on_event)
        pipeline.on_reply = self._on_reply

    def close(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)

    # ---- inputs ------------------------------------------------------------------------
    def _on_event(self, msg: dict) -> None:
        t, p = msg["type"], msg["payload"]
        if t == "power.sleeping":
            self.asleep, self._sleep_reason = True, p["reason"]
            self._pending.clear()
            self._stimuli.append(("sleep_entering", {}))
        elif t == "power.woke":
            self.asleep = False
            self._stimuli.append(("wake", {"cause": p["cause"], "sleep_reason": self._sleep_reason}))
            self._sleep_reason = None
        elif t == "touch.detected":
            self._stimuli.append(("touch", {}))
        elif t == "battery.updated":
            if p["power_state"] in ("LOW_BATTERY", "CRITICAL_BATTERY"):
                new = "low"
            elif p["warning"]:
                new = "warn"
            else:
                new = "ok"
            if new != self._battery:
                stim = {"low": "battery_low", "warn": "battery_warning", "ok": "battery_ok"}[new]
                self._battery = new
                self._stimuli.append((stim, {}))

    def _on_reply(self, reply) -> None:
        self._stimuli.append(("interaction", {"kind": reply.interaction or "conversation"}))

    # ---- loop ----------------------------------------------------------------------------
    def tick(self) -> None:
        now = self.clock()
        saw_interaction = False
        while self._stimuli:
            stim, ctx = self._stimuli.popleft()
            try:
                plan = self.behavior.react(stim, **ctx)
            except ValueError:
                continue
            saw_interaction |= stim == "interaction"
            self._handle(plan, now)
        if (not self.asleep and self.pipeline.state is State.IDLE and now - self._last_idle >= self.cfg.idle_tick_s):
            self._last_idle = now
            self._handle(self.behavior.react("idle_tick", memory_refs=self._memory_refs(now)), now)
        self._speak_pending(now)
        if self.state_store is not None and (saw_interaction or now - self._last_save >= self.cfg.save_interval_s):
            self.state_store.save(self.behavior.state)
            self._last_save = now

    def _handle(self, plan: Plan, now: float) -> None:
        if plan.animation and not self.asleep and self.pipeline.state is State.IDLE:
            key = plan.animation["name"]
            if plan.animation["loop"] and key == self._last_anim:
                pass                                                  # already showing it
            else:
                self._last_anim = key if plan.animation["loop"] else None
                try:
                    for t, payload in to_commands(plan):
                        self.pipeline.client.request(t, payload, timeout_s=2.0, until="accepted")
                except Exception:
                    self.stats["animation_failed"] += 1
        for intent in plan.intents:
            self._pending.append((now + self.cfg.pending_ttl_s, intent, plan.voice_style))

    def _speak_pending(self, now: float) -> None:
        if self._composing is not None:
            fut, intent, style = self._composing
            if not fut.done():
                return
            self._composing = None
            try:
                reply = fut.result()
            except Exception:
                self.stats["compose_errors"] += 1
                return
            if reply is None:
                self.stats["intent_unspeakable"] += 1
            elif self.asleep or not self.pipeline.speak(reply.text, reply.style):
                self.stats["dropped_busy"] += 1
            else:
                self.stats["spoken"] += 1
                self.stats[f"spoken:{intent.kind}"] += 1
                for g in intent.grounding:
                    if g.startswith("memory:"):
                        self._used[g[7:]] = now
        while self._pending and self._pending[0][0] < now:
            self._pending.popleft()
            self.stats["expired"] += 1
        if (self._composing is None and self._pending and not self.asleep and self.pipeline.state is State.IDLE):
            _, intent, style = self._pending.popleft()
            self._composing = (self._pool.submit(self.conversation.compose_intent, intent), intent, style)

    def _memory_refs(self, now: float) -> list[str]:
        if self.memory is None or not self.cfg.speak_memories:
            return []
        cands = []
        for cat in ("profile", "semantic", "goal_habit"):
            try:
                rows = self.memory.list(cat, include_pending=False)
            except Exception:
                continue
            for r in rows:
                if (r.get("key") in SKIP_KEYS or len(r["value"]) > self.cfg.max_memory_chars
                        or now - self._used.get(r["id"], -1e18) < self.cfg.memory_reuse_s):
                    continue
                cands.append(r["id"])
        self.rng.shuffle(cands)
        return cands[: self.cfg.memory_candidates]
