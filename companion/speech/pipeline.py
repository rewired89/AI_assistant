"""Push-to-talk voice turn: button -> capture -> endpoint -> STT -> (onboarding | responder) -> TTS -> speaker.

Non-blocking: call poll() from the host loop. Slow work (STT, reply, TTS) runs on worker threads and is
collected as futures, so a button press can always cancel or interrupt. The microphone is only ever opened
by an explicit press of the device button; audio is not stored (the capture is dropped as soon as STT has it)
and transcripts are passed on but never logged or kept here. Device-state problems are spoken from scripted
lines, never from the LLM.
"""
from __future__ import annotations

import re
import unicodedata
from collections import Counter, deque
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Protocol

from protocol import audio as aud
from protocol.schema_consts import ANIMATIONS, VOICE_STYLES

from ..application.device_client import Capture, CommandRejected, CommandTimeout, DeviceClient, PlaybackJob
from ..behavior import BehaviorEngine, to_commands
from ..interfaces import SpeechRecognizer, SpeechSynthesizer, TransportClosed
from .lines import DEFAULT_LINES
from .vad import EndpointDetector


class State(Enum):
    IDLE = "idle"
    LISTENING = "listening"
    TRANSCRIBING = "transcribing"
    THINKING = "thinking"
    SPEAKING = "speaking"


ALLOWED_AFTER_SPEECH = frozenset({"power.sleep"})


@dataclass
class Reply:
    text: str
    style: str = "normal"
    animation: str | None = None            # one-shot emotion shown after speaking (validated against the protocol enum)
    after_speech: tuple = ()                # ((protocol_type, payload), ...) sent only after the speech finished
    interaction: str | None = None          # growth category of the turn (conversation/teach/play/explore_topic)
    paced: bool = False                     # trusted scripted text only: "[pause N]" markers and `pauses` become real silence
    pauses: tuple = ()                      # seconds of silence played after the speech (paced replies only)


class Responder(Protocol):
    def respond(self, utterance: str) -> Reply: ...


@dataclass
class PipelineConfig:
    button: str = "main"
    max_listen_s: int = 15
    min_speech_s: float = 0.3
    trailing_silence_s: float = 1.0
    vad_threshold: float = 500.0
    stt_timeout_s: float = 20.0
    reply_timeout_s: float = 60.0
    tts_timeout_s: float = 20.0
    max_utterance_chars: int = 500
    max_reply_chars: int = 1200
    max_sentences: int = 8
    max_sentence_chars: int = 300


_SENT = re.compile(r"(?<=[.!?…])\s+")


def clean_transcript(text: str, limit: int) -> str:
    """STT output is untrusted text: drop control characters, collapse whitespace, cap length."""
    text = "".join(ch for ch in text if unicodedata.category(ch)[0] != "C" or ch in " \t\n")
    text = " ".join(text.split())[:limit].strip()
    return text if any(ch.isalnum() for ch in text) else ""


PAUSE_MARK = re.compile(r"\[pause (\d{1,2}(?:\.\d)?)\]")
MAX_PAUSE_S, MAX_TOTAL_PAUSE_S, CLIP_S, PACED_MAX_ITEMS = 60.0, 120.0, 10.0, 24


def plan_speech(text: str, paced: bool, trailing, max_sentences: int, max_chars: int) -> list:
    """Sentences (str) and silences (float seconds) in speaking order. Only trusted scripted text (paced=True) may
    contain silence; in anything else the markers are removed, so a model or a transcript can never make the
    robot go quiet on purpose. Each silence is capped, the total is capped, and long ones are cut into short clips."""
    if not paced:
        return split_sentences(" ".join(PAUSE_MARK.sub(" ", text).split()), max_sentences, max_chars)
    items: list = []
    budget = MAX_TOTAL_PAUSE_S

    def silence(sec: float) -> None:
        nonlocal budget
        sec = min(max(0.0, sec), MAX_PAUSE_S, budget)
        budget -= sec
        while sec > 0.05:
            items.append(min(sec, CLIP_S))
            sec -= CLIP_S

    pos = 0
    for m in PAUSE_MARK.finditer(text):
        items.extend(split_sentences(text[pos:m.start()], PACED_MAX_ITEMS, max_chars))
        silence(float(m.group(1)))
        pos = m.end()
    items.extend(split_sentences(text[pos:], PACED_MAX_ITEMS, max_chars))
    for sec in trailing or ():
        silence(float(sec))
    return items[:PACED_MAX_ITEMS]


def split_sentences(text: str, max_sentences: int, max_chars: int) -> list[str]:
    out: list[str] = []
    for part in _SENT.split(text.strip()):
        part = part.strip()
        while len(part) > max_chars:  # very long run-on: cut at the last space before the limit
            cut = part.rfind(" ", 0, max_chars)
            cut = cut if cut > 0 else max_chars
            out.append(part[:cut].strip())
            part = part[cut:].strip()
        if part:
            out.append(part)
    return out[:max_sentences]


class VoicePipeline:
    def __init__(self, client: DeviceClient, stt: SpeechRecognizer, tts: SpeechSynthesizer, responder: Responder,
                 clock: Callable[[], float], engine: BehaviorEngine | None = None, onboarding=None,
                 lines: dict[str, str] | None = None, config: PipelineConfig | None = None):
        self.client, self.stt, self.tts, self.responder = client, stt, tts, responder
        self.clock, self.engine, self.onboarding = clock, engine, onboarding
        self.lines = {**DEFAULT_LINES, **(lines or {})}
        self.cfg = config or PipelineConfig()
        self.state = State.IDLE
        self.stats: Counter = Counter()
        self.notes_total = 0                  # how many notes were ever written (the deque forgets old ones)
        self.log: deque = deque(maxlen=200)   # (state, note) only; never content
        self._presses = 0
        self._device_sleeping = False
        self.on_reply: Callable[[Reply], None] | None = None   # told about each model reply (no audio, no transcript)
        self.on_heard: Callable[[str], None] | None = None     # DEV RIGS ONLY (desk robot): the cleaned transcript, "" if none
        self._pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="voice")
        self._cap: Capture | None = None
        self._det: EndpointDetector | None = None
        self._fed = 0
        self._t_state = 0.0
        self._fut: Future | None = None
        self._speech: dict | None = None
        client.add_listener(self._on_event)

    # ---- public --------------------------------------------------------------------
    def close(self) -> None:
        self._abort_all()
        self._pool.shutdown(wait=False, cancel_futures=True)

    def speak(self, text: str, style: str = "normal", reply: Reply | None = None) -> bool:
        """Host-initiated speech (onboarding prompts, proactive intents). Never opens the microphone."""
        if self.state is not State.IDLE:
            return False
        self._start_speaking(text, style, reply)
        return self.state is State.SPEAKING

    def offer_onboarding(self, now: float, quiet: bool = False) -> bool:
        if self.onboarding is None or self.state is not State.IDLE:
            return False
        turn = self.onboarding.next_prompt(now, quiet=quiet)
        return bool(turn) and self.speak(turn.text)

    def poll(self) -> None:
        try:
            self.client.pump()
            if self._device_sleeping:        # radio is off: nothing we start can finish, so stand down
                self._device_sleeping = False
                self._presses = 0
                self.stats["device_sleep"] += 1
                self._note("device_sleeping")
                return self._abort_all()
            presses, self._presses = self._presses, 0
            getattr(self, "_poll_" + self.state.value)(presses)
        except (TransportClosed, CommandTimeout, CommandRejected) as e:
            self._note(f"device_error:{type(e).__name__}")
            self.stats["device_errors"] += 1
            self._abort_all()

    # ---- plumbing ------------------------------------------------------------------
    def _on_event(self, msg: dict) -> None:
        p = msg["payload"]
        if msg["type"] == "power.sleeping":
            self._device_sleeping = True
            return
        if msg["type"] == "button.pressed" and p["button"] == self.cfg.button and p["kind"] == "single":
            self._presses += 1

    def _note(self, note: str) -> None:
        self.log.append((self.state.value, note))
        self.notes_total += 1

    def _set(self, state: State) -> None:
        self.state, self._t_state = state, self.clock()

    def _stop_animation(self) -> None:
        """Looping animations (listening/thinking/talking) outrank idle, so they must be stopped explicitly."""
        try:
            self.client.request("animation.stop", {}, timeout_s=2.0, until="accepted")
        except TransportClosed:
            raise
        except Exception:
            pass  # nothing was playing

    def _animate(self, stimulus: str, stop_first: bool = False, **ctx) -> None:
        """Cosmetic: never lets an animation problem break the voice flow."""
        if self.engine is None:
            return
        try:
            if stop_first:
                self._stop_animation()
            for t, payload in to_commands(self.engine.react(stimulus, **ctx)):
                self.client.request(t, payload, timeout_s=2.0, until="accepted")
        except TransportClosed:
            raise
        except Exception as e:
            self._note(f"animation_failed:{type(e).__name__}")

    def _confused(self) -> None:
        try:
            self._stop_animation()
            self.client.request("animation.play", {"name": "confused", "loop": False, "priority": 5},
                                timeout_s=2.0, until="accepted")
        except TransportClosed:
            raise
        except Exception:
            pass

    def _to_idle(self, animate: bool = True) -> None:
        self._abort_all(keep_state=True)
        self._set(State.IDLE)
        if animate:
            self._animate("speaking_end", stop_first=True)
        else:
            self._stop_animation()

    def _abort_all(self, keep_state: bool = False) -> None:
        if self._fut:
            self._fut.cancel()
            self._fut = None
        if self._speech:
            job: PlaybackJob | None = self._speech.get("job")
            if job:
                job.cancel()
            for key in ("tts", "next_tts"):
                if self._speech.get(key):
                    self._speech[key].cancel()
            self._speech = None
        if self._cap is not None:
            self.client.captures.pop(self._cap.stream_no, None)
            self._cap = self._det = None
        if not keep_state:
            self._set(State.IDLE)

    def _timed_out(self, limit: float) -> bool:
        return self.clock() - self._t_state > limit

    # ---- IDLE -----------------------------------------------------------------------
    def _poll_idle(self, presses: int) -> None:
        if presses:
            self._begin_listen()

    def _begin_listen(self) -> None:
        try:
            cap = self.client.start_capture(max_seconds=self.cfg.max_listen_s)
        except CommandRejected as e:
            key = {"unsafe": "battery_low", "not_allowed": "mic_muted"}.get(e.code)
            self.stats["capture_refused"] += 1
            self._note(f"capture_refused:{e.code}")
            if key:
                self._start_speaking(self.lines[key], "calm")   # the device decided; we just explain
            return
        self._cap, self._fed = cap, 0
        self._det = EndpointDetector(self.cfg.vad_threshold, self.cfg.min_speech_s, self.cfg.trailing_silence_s)
        self._set(State.LISTENING)
        self.stats["turns"] += 1
        self._animate("listening_start")

    # ---- LISTENING ------------------------------------------------------------------
    def _poll_listening(self, presses: int) -> None:
        cap, det = self._cap, self._det
        for chunk in cap.chunks[self._fed:]:
            det.feed(chunk)
        self._fed = len(cap.chunks)
        why = ("button" if presses else "device" if cap.done else "silence" if det.endpoint
               else "overtime" if self._timed_out(self.cfg.max_listen_s + 3) else None)
        if why:
            self._finish_listen(why)

    def _finish_listen(self, why: str) -> None:
        cap, det = self._cap, self._det
        if not cap.done:
            self.client.stop_capture(cap, timeout_s=3.0)
        for chunk in cap.chunks[self._fed:]:
            det.feed(chunk)
        pcm, corrupt = cap.pcm, cap.corrupt
        self.client.captures.pop(cap.stream_no, None)   # audio is not kept
        self._cap = self._det = None
        self._note(f"listen_end:{why}")
        if corrupt:
            self.stats["corrupt_audio"] += 1
            self._start_speaking(self.lines["trouble_hearing"], "calm")
        elif not det.enough_speech:
            self.stats["no_speech"] += 1
            self._start_speaking(self.lines["didnt_hear"], "calm")
        else:
            self._fut = self._pool.submit(self.stt.transcribe, pcm)
            self._set(State.TRANSCRIBING)
            self._animate("thinking")

    # ---- TRANSCRIBING ---------------------------------------------------------------
    def _poll_transcribing(self, presses: int) -> None:
        if presses:
            return self._cancel_turn("cancelled_by_button")
        if self._fut.done():
            fut, self._fut = self._fut, None
            try:
                text = clean_transcript(fut.result(), self.cfg.max_utterance_chars)
            except Exception as e:
                self.stats["stt_errors"] += 1
                self._note(f"stt_error:{type(e).__name__}")
                return self._start_speaking(self.lines["trouble_hearing"], "calm")
            if self.on_heard is not None:
                try:
                    self.on_heard(text)
                except Exception:
                    pass
            if not text:
                self.stats["empty_transcript"] += 1
                self._note("heard_nothing")
                return self._start_speaking(self.lines["didnt_hear"], "calm")
            self._note("heard_text")
            return self._route(text)
        if self._timed_out(self.cfg.stt_timeout_s):
            self.stats["stt_timeouts"] += 1
            self._abort_all(keep_state=True)
            self._start_speaking(self.lines["trouble_hearing"], "calm")

    def _route(self, text: str) -> None:
        from ..psychology.safety.screen import RiskLevel, screen        # lazy: psychology imports this module
        # A safety signal always goes to the responder (where the gate answers), never into an onboarding answer.
        if self.onboarding is not None and self.onboarding.awaiting() and screen(text).level is RiskLevel.NONE:
            turn = self.onboarding.answer(text, self.clock())
            if not turn.passthrough:
                spoken = " ".join(t.text for t in (turn, turn.follow_up) if t and t.text)
                return self._start_speaking(spoken, "normal")
        self._fut = self._pool.submit(self.responder.respond, text)
        self._set(State.THINKING)

    # ---- THINKING -------------------------------------------------------------------
    def _poll_thinking(self, presses: int) -> None:
        if presses:
            return self._cancel_turn("cancelled_by_button")
        if self._fut.done():
            fut, self._fut = self._fut, None
            try:
                reply = fut.result()
                if not isinstance(reply, Reply) or not isinstance(reply.text, str):
                    raise TypeError("responder must return Reply")
            except Exception as e:
                self.stats["reply_errors"] += 1
                self._note(f"reply_error:{type(e).__name__}")
                return self._start_speaking(self.lines["trouble_thinking"], "calm")
            if self.on_reply is not None:
                try:
                    self.on_reply(reply)
                except Exception as e:
                    self._note(f"on_reply_failed:{type(e).__name__}")
            return self._start_speaking(reply.text[: self.cfg.max_reply_chars], reply.style, reply)
        if self._timed_out(self.cfg.reply_timeout_s):
            self.stats["reply_timeouts"] += 1
            self._abort_all(keep_state=True)
            self._start_speaking(self.lines["trouble_thinking"], "calm")

    def _cancel_turn(self, note: str) -> None:
        self._note(note)
        self.stats["cancelled"] += 1
        self._to_idle()

    # ---- SPEAKING -------------------------------------------------------------------
    def _start_speaking(self, text: str, style: str, reply: Reply | None = None) -> None:
        self._abort_all(keep_state=True)
        style = style if style in VOICE_STYLES else "normal"
        paced = bool(reply and reply.paced)
        sentences = plan_speech(clean_reply(text), paced, reply.pauses if paced else (), self.cfg.max_sentences,
                                self.cfg.max_sentence_chars)
        if not any(isinstance(x, str) for x in sentences):
            return self._to_idle(animate=False)
        self._speech = {"sentences": sentences, "i": 0, "style": style, "job": None, "reply": reply,
                        "tts": self._render(sentences[0], style), "next_tts": None}
        self._set(State.SPEAKING)
        self._animate("speaking_start", style=style)

    def _render(self, item, style: str) -> Future:
        """Audio for one item: the synthesizer for a sentence, plain silence for a pause (never touches the engine)."""
        if isinstance(item, float):
            f: Future = Future()
            f.set_result(bytes(int(item * aud.BYTES_PER_SECOND) // 2 * 2))
            return f
        return self._pool.submit(self.tts.synthesize, item, style)

    def _poll_speaking(self, presses: int) -> None:
        sp = self._speech
        if presses:  # barge-in
            self.stats["interrupted"] += 1
            self._note("interrupted_by_button")
            return self._to_idle()
        if sp["job"] is None:
            fut = sp["tts"]
            if fut.done():
                try:
                    pcm = fut.result()
                    if (not isinstance(pcm, (bytes, bytearray)) or not pcm or len(pcm) % 2
                            or len(pcm) > aud.BYTES_PER_SECOND * 30):
                        raise ValueError("synthesizer returned unusable audio")
                except Exception as e:
                    self.stats["tts_errors"] += 1
                    self._note(f"tts_error:{type(e).__name__}")
                    return self._confused_idle()
                sp["job"] = self.client.begin_playback(bytes(pcm), style=sp["style"])
                nxt = sp["i"] + 1
                if nxt < len(sp["sentences"]):  # synthesize the next sentence while this one plays
                    sp["next_tts"] = self._render(sp["sentences"][nxt], sp["style"])
            elif self._timed_out(self.cfg.tts_timeout_s):
                self.stats["tts_timeouts"] += 1
                return self._confused_idle()
            return
        try:
            done = sp["job"].step()
        except (CommandRejected, CommandTimeout):
            self.stats["playback_errors"] += 1
            self._note("playback_error")
            return self._to_idle()
        if done:
            sp["i"] += 1
            sp["job"] = None
            if sp["i"] >= len(sp["sentences"]):
                reply = sp.get("reply")
                self._to_idle()
                if reply is not None:
                    self._finish_reply(reply)
                return
            sp["tts"], sp["next_tts"] = sp["next_tts"], None
            self._t_state = self.clock()

    def _finish_reply(self, reply: Reply) -> None:
        """Runs only after the speech played to the end (a barge-in drops these on purpose).
        Host-validated, whitelisted requests; the device validates them again."""
        if reply.animation in ANIMATIONS:
            try:
                self.client.request("animation.play", {"name": reply.animation, "loop": False, "priority": 5},
                                    timeout_s=2.0, until="accepted")
            except TransportClosed:
                raise
            except Exception:
                self._note("emotion_failed")
        for mtype, payload in reply.after_speech:
            if mtype not in ALLOWED_AFTER_SPEECH:
                self.stats["blocked_actions"] += 1
                continue
            try:
                self.client.request(mtype, payload, timeout_s=5.0)
                self.stats["actions_sent"] += 1
            except TransportClosed:
                raise
            except Exception as e:
                self.stats["action_errors"] += 1
                self._note(f"action_failed:{type(e).__name__}")

    def _confused_idle(self) -> None:
        self._abort_all(keep_state=True)
        self._set(State.IDLE)
        self._confused()


def clean_reply(text: str) -> str:
    return " ".join("".join(ch for ch in text if unicodedata.category(ch)[0] != "C" or ch in " \t\n").split())
