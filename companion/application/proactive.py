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
from datetime import datetime, timedelta
from typing import Callable

from ..behavior import BehaviorEngine, Intent, Plan, to_commands
from ..conversation.engine import ConversationEngine
from ..scheduling.sleep import build_sleep_payload
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
    bedtime: bool = True              # during quiet hours, ask the device to sleep after she has been left alone
    bedtime_idle_s: float = 600.0
    bedtime_retry_s: float = 1800.0


class ProactiveDirector:
    def __init__(self, behavior: BehaviorEngine, conversation: ConversationEngine, pipeline: VoicePipeline,
                 clock: Callable[[], float], memory=None, state_store=None, config: DirectorConfig | None = None,
                 rng: random.Random | None = None, psychology=None, insights=None, checkin=None, checkin_blocked=lambda: False, reminders=None, remind_talk=None,
                 reminder_name=lambda: None):
        self.behavior, self.conversation, self.pipeline, self.clock = behavior, conversation, pipeline, clock
        self.memory, self.state_store, self.cfg = memory, state_store, config or DirectorConfig()
        self.rng = rng or random.Random()
        self.psychology = psychology
        self.reminders, self.remind_talk, self.reminder_name = reminders, remind_talk, reminder_name   # her reminders (ReminderStore, RemindTalk)
        self._rem_active: dict | None = None
        self._rem_last_tick = 0.0
        self.checkin, self.checkin_blocked = checkin, checkin_blocked      # CheckIn (optional) and a 'not now' test (safety signal, ...)
        self.insights = insights                            # DailyInsights (optional)
        self._insight: tuple | None = None                   # (Script, spoken?) being spoken right now
        self._queued_followups: set[str] = set()
        self.stats: Counter = Counter()
        self.asleep = False
        self._sleep_reason: str | None = None
        self._stimuli: deque = deque()
        self._pending: deque = deque()                      # (expires, Intent, voice_style)
        self._composing: tuple[Future, Intent, str] | None = None
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="proactive")
        self._used: dict[str, float] = {}
        self._last_idle = self._last_save = self._last_activity = clock()
        self._last_bedtime_try = -1e18
        self._battery = "ok"
        self._last_anim: str | None = None
        pipeline.client.add_listener(self._on_event)
        pipeline.on_reply = self._on_reply

    def close(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)

    # ---- inputs ------------------------------------------------------------------------
    def _on_event(self, msg: dict) -> None:
        t, p = msg["type"], msg["payload"]
        if t in ("button.pressed", "touch.detected", "power.woke"):
            self._last_activity = self.clock()
        if t == "power.sleeping":
            self.asleep, self._sleep_reason = True, p["reason"]
            self._pending.clear()
            self._stimuli.append(("sleep_entering", {}))
        elif t == "power.woke":
            self.asleep = False
            # the device remembers why it slept, even across a host restart; our own memory of it is only a fallback
            self._stimuli.append(("wake", {"cause": p["cause"], "sleep_reason": p.get("sleep_reason") or self._sleep_reason}))
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
        if reply.interaction == "none":                     # safety and reflection turns never count as growth or play
            return
        self._last_activity = self.clock()
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
        self._bedtime(now)
        self._queue_followups(now)
        self._deliver_reminders(now)
        self._deliver_insight(now)
        self._deliver_checkin(now)
        self._speak_pending(now)
        if self.state_store is not None and (saw_interaction or now - self._last_save >= self.cfg.save_interval_s):
            self.state_store.save(self.behavior.state)
            self._last_save = now

    def _bedtime(self, now: float) -> None:
        """Late at night, once she has been left alone for a while, ask the device to sleep until quiet hours end.
        The device may refuse; the touch or a button wakes it again."""
        if (not self.cfg.bedtime or self.asleep or self.pipeline.state is not State.IDLE
                or not self.behavior._quiet_hour(now) or now - self._last_activity < self.cfg.bedtime_idle_s
                or now - self._last_bedtime_try < self.cfg.bedtime_retry_s):
            return
        self._last_bedtime_try = now
        tz = self.behavior.state.tz
        local = datetime.fromtimestamp(now, tz)
        wake = local.replace(hour=self.behavior.settings.quiet_hours[1] % 24, minute=0, second=0, microsecond=0)
        if wake <= local:
            wake += timedelta(days=1)
        try:
            payload = build_sleep_payload(wake, local, reason="scheduled", requested_by="system")
            self.pipeline.client.request("power.sleep", payload, timeout_s=2.0, until="accepted")
            self.stats["bedtime_requested"] += 1
        except Exception:
            self.stats["bedtime_failed"] += 1

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
                if intent.kind == "followup_due" and self.psychology is not None:
                    self.psychology.note_followup_spoken(intent.data["fid"])
                    self._queued_followups.discard(intent.data["fid"])
                for g in intent.grounding:
                    if g.startswith("memory:"):
                        self._used[g[7:]] = now
        while self._pending and self._pending[0][0] < now:
            _, old, _ = self._pending.popleft()
            self.stats["expired"] += 1
            if old.kind == "followup_due":
                self._queued_followups.discard(old.data["fid"])   # still due: it will be queued again
        if (self._composing is None and self._pending and not self.asleep and self.pipeline.state is State.IDLE):
            _, intent, style = self._pending.popleft()
            self._composing = (self._pool.submit(self.conversation.compose_intent, intent), intent, style)

    def _deliver_insight(self, now: float) -> None:
        """One scheduled psychology discovery a day, only when nothing else is happening. Never retried within the day
        if it could not start (she is mid-conversation): the next idle moment inside the window tries again; after the
        window the day is simply skipped."""
        ins = self.insights
        if ins is None or self.asleep or self._composing is not None or self._pending or self.pipeline.state is not State.IDLE:
            return
        script = ins.prepare(now)
        if script is None:
            return
        if ins.mode == "text":
            ins.commit(script, now, spoken=False)
            self.stats["insight_text"] += 1
        elif self.pipeline.speak(script.text, "normal"):
            ins.commit(script, now, spoken=True)
            self.stats["insight_spoken"] += 1

    def _deliver_reminders(self, now: float) -> None:
        """Speaks the reminders she asked for. A reminder is marked spoken only after the speech really completed; if the robot is asleep,
        offline, busy or the speech failed, the record says so and the phone keeps it. Reminders speak even in quiet mode (she asked for them);
        her own quiet hours were applied when each occurrence was recorded."""
        rs = self.reminders
        if rs is None:
            return
        try:
            if now - self._rem_last_tick >= 2.0:
                self._rem_last_tick = now
                rs.tick(now)
            a = self._rem_active
            if a is not None and self.pipeline.state is not State.SPEAKING:
                self._rem_active = None
                done = [r for k, r in self.pipeline.client.playback_results.items() if k not in a["streams"]]
                ok = (self.pipeline.stats["interrupted"] == a["interrupted"] and self.pipeline.stats["tts_errors"] == a["errors"] and done
                      and all(r.get("result") == "completed" for r in done))
                rs.mark_robot(a["id"], "spoken" if ok else "failed")
                self.stats["reminder_spoken" if ok else "reminder_failed"] += 1
                return
            if (a is not None or self.asleep or self._composing is not None or self._pending or self.pipeline.state is not State.IDLE
                    or not self.behavior.host_online or self._battery != "ok" or self.checkin_blocked()):
                return
            for occ in rs.due_for_robot(now):
                rem = rs.get(occ["reminder_id"])
                if rem is None:
                    continue
                from ..productivity.reminders import compose
                style = "playful" if self.behavior.p.value("playful") >= 0.6 else "gentle"
                text = compose(rem, occ["due_at"], self.reminder_name(), style)
                streams = set(self.pipeline.client.playback_results)
                if self.pipeline.speak(text, "normal"):
                    rs.mark_robot(occ["id"], "speaking", text)
                    self._rem_active = {"id": occ["id"], "streams": streams, "interrupted": self.pipeline.stats["interrupted"],
                                        "errors": self.pipeline.stats["tts_errors"]}
                    if self.remind_talk is not None:
                        self.remind_talk.note_spoken(occ["id"], now)
                    self.stats["reminder_started"] += 1
                return
        except Exception:
            self.stats["reminder_errors"] += 1                      # a reminder bug must never stop the companion's loop

    def _deliver_checkin(self, now: float) -> None:
        """One 'how was your day?' a day when nothing else is going on. Never right after a discovery, a safety signal or on
        a low battery; if it could not start it tries again at the next idle moment inside the window, then the day is skipped."""
        ci = self.checkin
        if ci is None or self.asleep or self._composing is not None or self._pending or self.pipeline.state is not State.IDLE:
            return
        if self._battery != "ok" or self.checkin_blocked():
            return
        ins = getattr(self, "insights", None)
        if ins is not None and ins.offer and now - ins.offer["t"] < 30 * 60:
            return
        text = ci.question(now)
        if text and self.pipeline.speak(text, "normal"):
            ci.asked(now)
            self.stats["checkin_asked"] += 1

    def _queue_followups(self, now: float) -> None:
        """Reminders she asked for (content-free). Spoken even in quiet mode, but never while asleep or busy."""
        if self.psychology is None or self.asleep:
            return
        for f in self.psychology.due_followups():
            if f["id"] not in self._queued_followups:
                self._queued_followups.add(f["id"])
                self._pending.append((now + self.cfg.pending_ttl_s, Intent("followup_due", tone="calm", data={"fid": f["id"]}), "calm"))

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
