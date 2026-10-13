"""The whole companion, built from a config file, talking to a simulated robot over real TLS."""
import json
import socket
import threading
import time

import pytest
import yaml

from companion.app import AppConfig, Companion, ConfigProblem
from companion.application.clock import SystemClock
from companion.application.ws_transport import client_ssl_context
from companion.conversation import FakeLanguageModel
from companion.memory import EncryptionError, MemoryStore
from companion.security import SecretStore
from companion.speech import State
from companion.speech.fakes import FakeRecognizer, FakeSynthesizer, silence, tone
from simulator import SimulatedDevice, Storage
from simulator.ws_link import DeviceLink
from tests.integration.test_wss import pki, wait_for  # noqa: F401

CLOCK = SystemClock()
DID = "robot-01"


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def home(tmp_path, pki):
    port = free_port()
    store = SecretStore(tmp_path / "keys.json", CLOCK)
    bundle = store.create_device(DID, pki["ca_cert"].read_text(), f"wss://localhost:{port}")
    cfg = {"species": "ninetales", "device_id": DID, "secrets": "keys.json", "data_dir": "data",
           "tls": {"cert": str(pki["server_cert"]), "key": str(pki["server_key"])},
           "listen": {"host": "127.0.0.1", "port": port}}
    (tmp_path / "companion.yaml").write_text(yaml.safe_dump(cfg))
    return dict(tmp=tmp_path, bundle=bundle, port=port, pki=pki, cfg=cfg)


class Running:
    def __init__(self, home, stt=None, tts=None, llm=None, cfg_path=None):
        self.home = home
        self.app = Companion(AppConfig.load(cfg_path or home["tmp"] / "companion.yaml"), clock=CLOCK,
                             stt=stt, tts=tts, llm=llm)
        self.dev = SimulatedDevice(None, Storage(home["tmp"] / "flash.json"), CLOCK)
        self.dev.provision(home["bundle"])
        self.dev.boot()
        self.link = DeviceLink(self.dev, home["bundle"]["url"], client_ssl_context(str(home["pki"]["ca_cert"])),
                               tick_s=0.02, backoff_base_s=0.05, backoff_cap_s=0.2)
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self.app.run, args=(self.stop,), kwargs={"period_s": 0.01}, daemon=True)

    def __enter__(self):
        self.thread.start()
        self.link.start()
        assert wait_for(lambda: self.app.transport.connected)
        return self

    def __exit__(self, *a):
        self.link.stop()
        self.stop.set()
        self.thread.join(10)

    def talk(self, speech_s=0.6):
        self.link.inject(lambda: self.dev.press_button("single"))
        assert wait_for(lambda: self.app.pipeline.state is State.LISTENING, 5)
        self.link.call(lambda: self.dev.feed_mic(tone(speech_s)))
        self.link.call(lambda: self.dev.feed_mic(silence(1.3)))

    def settle(self, turns, timeout=25):
        p = self.app.pipeline
        return wait_for(lambda: p.state is State.IDLE and p.stats["turns"] >= turns and self.dev.playback is None
                        and self.app.director._composing is None, timeout)


def test_config_is_validated_before_anything_starts(home):
    base = dict(home["cfg"])
    p = home["tmp"] / "bad.yaml"
    for change, msg in [({"surprise": 1}, "unknown settings"), ({"species": "nope"}, "unknown species"),
                        ({"timezone": "Mars/Base"}, "timezone"), ({"stt": {"engine": "whisper"}}, "stt.model"),
                        ({"memory": {"encrypt": True}}, "passphrase_env"), ({"llm": {"engine": "x"}}, "llm.engine"),
                        ({"tts": {"engine": "espeak", "volume": 3}}, "unknown settings"),
                        ({"tts": {"engine": "kokoro"}}, "tts.model"), ({"tts": {"engine": "piper"}}, "tts.engine")]:
        p.write_text(yaml.safe_dump({**base, **change}))
        with pytest.raises(ConfigProblem, match=msg):
            AppConfig.load(p)
    p.write_text(yaml.safe_dump({k: v for k, v in base.items() if k != "tls"}))
    with pytest.raises(ConfigProblem, match="tls"):
        AppConfig.load(p)


def test_onboarding_then_conversation_end_to_end(home):
    stt = FakeRecognizer(["Sam", "Kit", "what is your name"])
    tts = FakeSynthesizer()
    llm = FakeLanguageModel()
    with Running(home, stt=stt, tts=tts, llm=llm) as r:
        # she greets first and asks the first onboarding question without anyone pressing a button
        assert wait_for(lambda: tts.calls, 10)
        assert r.app.pipeline.state in (State.SPEAKING, State.IDLE) or r.app.onboarding.awaiting
        assert wait_for(lambda: r.app.pipeline.state is State.IDLE and r.dev.playback is None, 15)
        r.talk()
        assert r.settle(1)
        assert r.app.onboarding.user_name() == "Sam"
        assert wait_for(lambda: r.app.pipeline.state is State.IDLE and r.dev.playback is None, 15)
        r.talk()
        assert r.settle(2)
        assert r.app.onboarding.core_done and r.app.onboarding.answers()["companion_name"] == "Kit"
        before = len(tts.calls)
        assert wait_for(lambda: r.app.pipeline.state is State.IDLE and r.dev.playback is None, 15)
        r.talk()
        assert r.settle(3)
        assert llm.calls and len(tts.calls) > before             # an ordinary turn reached the model and was spoken
        assert all(x["result"] == "completed" and x["underruns"] == 0 for x in r.app.client.playback_results.values())
        assert r.dev.audio_rejected == 0
    assert (home["tmp"] / "data" / "onboarding.json").exists()


def test_restart_keeps_what_she_told_it(home):
    with Running(home, stt=FakeRecognizer(["Sam"]), tts=FakeSynthesizer(), llm=FakeLanguageModel()) as r:
        assert wait_for(lambda: r.app.pipeline.state is State.IDLE and r.app.onboarding.awaiting and r.dev.playback is None, 15)
        r.talk()
        assert r.settle(1)
        assert r.app.onboarding.user_name() == "Sam"
    app2 = Companion(AppConfig.load(home["tmp"] / "companion.yaml"), clock=CLOCK, transport=_NullTransport(),
                     authenticator=SecretStore(home["tmp"] / "keys.json", CLOCK).authenticator(DID, CLOCK))
    try:
        assert app2.onboarding.user_name() == "Sam"
    finally:
        app2.close()


class _NullTransport:
    def send(self, raw): pass
    def send_binary(self, raw): pass
    def receive(self): return []
    def receive_binary(self): return []


def test_unconfigured_engines_fail_politely_instead_of_pretending(home):
    with Running(home) as r:                       # no stt/tts/llm configured at all
        assert wait_for(lambda: r.app.pipeline.state is State.IDLE, 5)
        r.talk()
        time.sleep(1.5)
        assert wait_for(lambda: r.app.pipeline.state is State.IDLE, 10)
        assert r.thread.is_alive() and r.dev.capture is None
        assert r.app.pipeline.stats["stt_errors"] == 1   # failed visibly instead of inventing a transcript


def test_encrypted_memory_is_wired_and_needs_the_passphrase(home, monkeypatch):
    cfg = {**home["cfg"], "memory": {"encrypt": True, "passphrase_env": "NT_TEST_PASS"}}
    p = home["tmp"] / "enc.yaml"
    p.write_text(yaml.safe_dump(cfg))
    monkeypatch.delenv("NT_TEST_PASS", raising=False)
    with pytest.raises(ConfigProblem, match="NT_TEST_PASS"):
        Companion(AppConfig.load(p), clock=CLOCK, transport=_NullTransport(),
                  authenticator=SecretStore(home["tmp"] / "keys.json", CLOCK).authenticator(DID, CLOCK))
    monkeypatch.setenv("NT_TEST_PASS", "a long enough passphrase")
    app = Companion(AppConfig.load(p), clock=CLOCK, transport=_NullTransport(),
                    authenticator=SecretStore(home["tmp"] / "keys.json", CLOCK).authenticator(DID, CLOCK))
    app.memory.add({"category": "profile", "value": "loves jasmine tea"})
    app.close()
    assert b"jasmine" not in (home["tmp"] / "data" / "memory.db").read_bytes()
    with pytest.raises(EncryptionError):
        MemoryStore(home["tmp"] / "data" / "memory.db", "her", CLOCK)


def test_failed_startup_leaves_nothing_open(home):
    cfg = {**home["cfg"], "tls": {"cert": "missing.pem", "key": "missing.key"}}
    p = home["tmp"] / "x.yaml"
    p.write_text(yaml.safe_dump(cfg))
    with pytest.raises(Exception):
        Companion(AppConfig.load(p), clock=CLOCK)


def test_phone_app_config_is_validated_and_the_voice_to_phone_path_works(home):
    import yaml
    cfgp = home["tmp"] / "companion.yaml"
    raw = yaml.safe_load(cfgp.read_text())
    for bad in ({"enabled": "yes"}, {"colour": 1}, {"port": "x"}):
        (home["tmp"] / "bad.yaml").write_text(yaml.safe_dump({**raw, "webapp": bad}))
        with pytest.raises(ConfigProblem):
            AppConfig.load(home["tmp"] / "bad.yaml")
    raw["webapp"] = {"enabled": True, "host": "127.0.0.1", "port": 0}
    cfgp.write_text(yaml.safe_dump(raw))
    app = Companion(AppConfig.load(cfgp), clock=CLOCK, transport=_NullTransport(),
                    authenticator=SecretStore(home["tmp"] / "keys.json", CLOCK).authenticator(DID, CLOCK))
    try:
        app.start()
        assert app.webapp.url.startswith("http://localhost:") and app.notes_talk.phone_hint
        r = app.conversation.respond("write this down: I want a magnetic robot enclosure")
        assert "review it on your phone" in r.text
        assert app.productivity.notes()[0]["body"] == "I want a magnetic robot enclosure"
    finally:
        app.close()


def test_console_say_lines_go_through_the_speech_path(home):
    """Dev console (`--console`): a typed line is spoken via VoicePipeline.speak, one at a time, and the outcome is reported."""
    with Running(home) as r:
        said, out = [], []
        r.app.pipeline.speak = lambda text, *a, **k: said.append(text) or True
        r.app.console_out = out.append
        r.app.console_say.put("hello there")
        assert wait_for(lambda: said == ["hello there"], 5)
        assert wait_for(lambda: out and "speaking" in out[-1], 5)


def test_console_verbose_prints_step_names_not_content(home):
    with Running(home) as r:
        out = []
        r.app.console_out = out.append
        r.app.console_verbose = True
        r.app.pipeline._note("listen_start")
        assert wait_for(lambda: any("listen_start" in line for line in out), 5)
