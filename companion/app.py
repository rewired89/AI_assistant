"""Composition root: reads one config file and wires every part into a running companion.

    python -m companion --config config\\companion.yaml

Nothing new is decided here. Each rule (consent, safety, privacy, hardware ownership) lives in the part that owns it;
this file only constructs the parts, hands them the right collaborators, and runs the loop. Engines that are not
configured are replaced by honest stand-ins that fail visibly (the robot says it cannot hear/think right now) rather
than by fakes that pretend."""
from __future__ import annotations

import os
import random
import signal
import threading
import time
from dataclasses import dataclass, field
from datetime import timezone, tzinfo
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

from .application.clock import SystemClock
from .application.device_client import DeviceClient
from .application.onboarding import Onboarding, load_copy
from .application.proactive import ProactiveDirector
from .application.ws_transport import WebSocketHostTransport, server_ssl_context
from .conversation import ConversationEngine, LLMUnavailable, OllamaLanguageModel
from .conversation.dialogue import LinePicker, load_templates
from .interfaces import LanguageModel, SpeechRecognizer, SpeechSynthesizer
from .behavior import BehaviorEngine
from .learning import MemoryTalk, RoutineLearner
from .memory import MemoryStore, ReflectionVault
from .personality import config as pc
from .psychology.engine import PsychologyEngine
from .psychology.safety import SafetyGate
from .psychology.settings import PsychologySettings
from .security import SecretStore
from .speech import VoicePipeline, load_lines
from .tamagotchi import StateStore, load_rules

ROOT = Path(__file__).resolve().parent.parent
KNOWN = {"species", "user_id", "timezone", "data_dir", "device_id", "secrets", "listen", "tls", "stt", "tts", "llm", "memory"}


class ConfigProblem(Exception):
    pass


@dataclass
class AppConfig:
    species: str
    device_id: str
    secrets: Path
    tls_cert: Path
    tls_key: Path
    data_dir: Path
    user_id: str = "her"
    timezone: str = "UTC"
    host: str = "127.0.0.1"
    port: int = 8443
    stt: dict = field(default_factory=dict)
    tts: dict = field(default_factory=dict)
    llm: dict = field(default_factory=dict)
    memory: dict = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path | str) -> "AppConfig":
        p = Path(path)
        try:
            raw = yaml.safe_load(p.read_text()) or {}
        except (OSError, yaml.YAMLError) as e:
            raise ConfigProblem(f"cannot read {p}: {e}") from None
        if not isinstance(raw, dict):
            raise ConfigProblem("config must be a mapping")
        unknown = set(raw) - KNOWN
        if unknown:
            raise ConfigProblem(f"unknown settings: {sorted(unknown)}")
        base = p.parent

        def need(k):
            if k not in raw:
                raise ConfigProblem(f"missing setting: {k}")
            return raw[k]

        def path_of(v):
            q = Path(str(v)).expanduser()
            return q if q.is_absolute() else base / q
        tls, listen = need("tls"), raw.get("listen") or {}
        if not isinstance(tls, dict) or not {"cert", "key"} <= set(tls):
            raise ConfigProblem("tls needs cert and key")
        cfg = cls(species=str(need("species")), device_id=str(need("device_id")), secrets=path_of(need("secrets")),
                  tls_cert=path_of(tls["cert"]), tls_key=path_of(tls["key"]), data_dir=path_of(need("data_dir")),
                  user_id=str(raw.get("user_id", "her")), timezone=str(raw.get("timezone", "UTC")),
                  host=str(listen.get("host", "127.0.0.1")), port=int(listen.get("port", 8443)),
                  stt=raw.get("stt") or {}, tts=raw.get("tts") or {}, llm=raw.get("llm") or {}, memory=raw.get("memory") or {})
        cfg.validate()
        return cfg

    def validate(self) -> None:
        if not (ROOT / "species" / self.species / "personality.yaml").exists():
            raise ConfigProblem(f"unknown species {self.species!r}")
        try:
            ZoneInfo(self.timezone)
        except Exception:
            raise ConfigProblem(f"unknown timezone {self.timezone!r}") from None
        if not 0 < self.port < 65536:
            raise ConfigProblem("listen.port out of range")
        for name, sec, allowed in (("stt", self.stt, {"engine", "model", "language"}), ("tts", self.tts, {"engine", "voice", "model", "speed"}),
                                   ("llm", self.llm, {"engine", "model", "url", "allow_remote"}),
                                   ("memory", self.memory, {"encrypt", "passphrase_env"})):
            if set(sec) - allowed:
                raise ConfigProblem(f"{name}: unknown settings {sorted(set(sec) - allowed)}")
        if self.stt.get("engine", "none") not in ("none", "whisper") or self.tts.get("engine", "none") not in ("none", "espeak", "kokoro"):
            raise ConfigProblem("stt.engine is none|whisper, tts.engine is none|espeak|kokoro")
        if self.tts.get("engine") == "kokoro" and not self.tts.get("model"):
            raise ConfigProblem("tts.model must point at the folder from: python -m tools.fetch_models kokoro")
        if self.stt.get("engine") == "whisper" and not self.stt.get("model"):
            raise ConfigProblem("stt.model must point at a downloaded model folder")
        if self.llm.get("engine", "none") not in ("none", "ollama"):
            raise ConfigProblem("llm.engine is none|ollama")
        if self.llm.get("engine") == "ollama" and not self.llm.get("model"):
            raise ConfigProblem("llm.model is required")
        if self.memory.get("encrypt") and not self.memory.get("passphrase_env"):
            raise ConfigProblem("memory.encrypt needs memory.passphrase_env (the NAME of an environment variable)")


class _NoEars(SpeechRecognizer):
    def transcribe(self, pcm: bytes) -> str:
        raise RuntimeError("no speech recognizer configured")


class _NoVoice(SpeechSynthesizer):
    def synthesize(self, text: str, style: str = "normal", speed: float = 1.0) -> bytes:
        raise RuntimeError("no speech synthesizer configured")


class _NoBrain(LanguageModel):
    is_local = True

    def complete(self, system, messages, tools=None):
        raise LLMUnavailable("no language model configured")


class Companion:
    """Everything wired. `step()` is one pass of the host loop; `run()` repeats it until stopped."""

    def __init__(self, cfg: AppConfig, clock=None, transport=None, stt=None, tts=None, llm=None, authenticator=None):
        self.cfg, self.clock = cfg, clock or SystemClock()
        self.tz: tzinfo = ZoneInfo(cfg.timezone) if cfg.timezone != "UTC" else timezone.utc
        sp = ROOT / "species" / cfg.species
        data = cfg.data_dir
        data.mkdir(parents=True, exist_ok=True)
        self._closers = []
        try:
            self._build(sp, data, transport, stt, tts, llm, authenticator)
        except Exception:
            self.close()
            raise

    def _passphrase(self) -> str | None:
        if not self.cfg.memory.get("encrypt"):
            return None
        name = self.cfg.memory["passphrase_env"]
        pw = os.environ.get(name)
        if not pw:
            raise ConfigProblem(f"memory is set to encrypted but environment variable {name} is empty")
        return pw

    def _build(self, sp, data, transport, stt, tts, llm, authenticator):
        cfg, clock = self.cfg, self.clock
        pw = self._passphrase()
        self.memory = MemoryStore(data / "memory.db", cfg.user_id, clock, pw)
        self.vault = ReflectionVault(data / "reflections.db", cfg.user_id, clock, pw)
        self._closers += [self.memory.close, self.vault.close]

        base = pc.load(sp / "personality.yaml")
        # Optional per-character growth rules: Cubone has none yet, so it shares Ninetales' (rates are not species-bound).
        growth = sp / "growth.yaml" if (sp / "growth.yaml").exists() else ROOT / "species" / "ninetales" / "growth.yaml"
        rules = load_rules(growth)
        self.state_store = StateStore(data / "tamagotchi.json", rules)
        self.state = self.state_store.load(clock(), self.tz)

        if transport is None:
            secrets = SecretStore(cfg.secrets, clock, os.environ.get("NINETAILS_KEY_PASSPHRASE") or None)
            transport = WebSocketHostTransport(server_ssl_context(str(cfg.tls_cert), str(cfg.tls_key)), host=cfg.host,
                                               port=cfg.port, pre_auth_check=secrets.mac_gate(clock, only_device=cfg.device_id))
            authenticator = secrets.authenticator(cfg.device_id, clock)
        self.transport = transport
        self.client = DeviceClient(transport, authenticator, clock)

        self.onboarding = Onboarding(data / "onboarding.json", load_copy(sp), base, memory=self.memory, device=self.client)
        eff = self.onboarding.effective_config

        self.llm = llm or self._make_llm()
        self.settings = PsychologySettings(data / "psychology.json", clock)
        self.gate = SafetyGate(clock, lambda: self.settings.region)
        self.psychology = PsychologyEngine(
            self.settings, self.vault, clock, gate=self.gate, tz=self.tz, llm=self.llm,
            feature=lambda: eff()["behavior"]["psychological_growth_features"])
        self.learner = RoutineLearner(data / "routines.json", clock, self.tz, self.memory)
        self.memory_talk = MemoryTalk(self.memory, self.learner, clock=clock)
        self.conversation = ConversationEngine(
            self.llm, eff, self.state, clock, memory=self.memory, user_name=self.onboarding.user_name, tz=self.tz,
            dialogue=LinePicker(load_templates(sp), random.Random()), safety=self.gate, psychology=self.psychology,
            learner=self.learner, memory_talk=self.memory_talk)
        self.personality = pc.Personality(eff())
        self.behavior = BehaviorEngine(self.personality, self.state, clock, random.Random())
        self.pipeline = VoicePipeline(self.client, stt or self._make_stt(), tts or self._make_tts(), self.conversation, clock,
                                      engine=self.behavior, onboarding=self.onboarding, lines=load_lines(sp))
        self.director = ProactiveDirector(self.behavior, self.conversation, self.pipeline, clock, memory=self.memory,
                                          state_store=self.state_store, psychology=self.psychology)
        self._closers += [self.pipeline.close, self.director.close]
        self._started = False

    def _make_stt(self) -> SpeechRecognizer:
        if self.cfg.stt.get("engine") == "whisper":
            from .speech.engines import WhisperRecognizer
            return WhisperRecognizer(self.cfg.stt["model"], language=self.cfg.stt.get("language", "en"))
        return _NoEars()

    def _make_tts(self) -> SpeechSynthesizer:
        if self.cfg.tts.get("engine") == "kokoro":
            from .speech.engines import KokoroSynthesizer
            return KokoroSynthesizer(self.cfg.tts["model"], voice=self.cfg.tts.get("voice", "af_heart"))
        if self.cfg.tts.get("engine") == "espeak":
            from .speech.engines import EspeakSynthesizer
            return EspeakSynthesizer(voice=self.cfg.tts.get("voice", "en-us"))
        return _NoVoice()

    def _make_llm(self) -> LanguageModel:
        if self.cfg.llm.get("engine") == "ollama":
            return OllamaLanguageModel(self.cfg.llm["model"], base_url=self.cfg.llm.get("url", "http://127.0.0.1:11434"),
                                       allow_remote=bool(self.cfg.llm.get("allow_remote", False)))
        return _NoBrain()

    # ---- run ---------------------------------------------------------------------------------------
    def start(self) -> None:
        if hasattr(self.transport, "start") and not self._started:
            self.transport.start()
        self._started = True

    def step(self) -> None:
        self.personality.cfg = self.onboarding.effective_config()      # onboarding answers take effect live
        self.pipeline.poll()
        self.director.tick()
        if not self.director.asleep:
            self.pipeline.offer_onboarding(self.clock())
        self.onboarding.sync_device()

    def run(self, stop: threading.Event, period_s: float = 0.05) -> None:
        self.start()
        try:
            while not stop.is_set():
                try:
                    self.step()
                except Exception:                      # one bad pass must not take the companion down
                    self.errors = getattr(self, "errors", 0) + 1
                stop.wait(period_s)
        finally:
            self.close()

    def close(self) -> None:
        for c in reversed(self._closers):
            try:
                c()
            except Exception:
                pass
        self._closers = []
        if getattr(self, "state_store", None) and getattr(self, "state", None):
            try:
                self.state_store.save(self.state)
            except Exception:
                pass
        if hasattr(getattr(self, "transport", None), "stop"):
            try:
                self.transport.stop()
            except Exception:
                pass


def main(argv: list[str] | None = None) -> int:
    import argparse
    import sys
    ap = argparse.ArgumentParser(prog="companion")
    ap.add_argument("--config", required=True)
    a = ap.parse_args(argv)
    try:
        app = Companion(AppConfig.load(a.config))
    except (ConfigProblem, Exception) as e:
        print(f"error: {type(e).__name__}: {e}", file=sys.stderr)
        return 1
    stop = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, lambda *_: stop.set())
    print(f"companion running on {app.cfg.host}:{app.cfg.port}; Ctrl+C to stop")
    app.run(stop)
    return 0
