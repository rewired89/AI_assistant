"""Regressions for defects an independent adversarial review reproduced. Each test fails on the old behavior."""
import json

import pytest

from companion.application.onboarding import Onboarding, load_copy
from companion.conversation import FakeLanguageModel
from companion.conversation.safety import violations
from companion.personality import config as pc
from companion.psychology.safety import SafetyGate
from companion.psychology.safety.screen import RiskLevel, screen
from companion.security import SecretStore
from companion.textnorm import normalize
from protocol import Authenticator, build
from simulator import SimulatedDevice, Storage
from simulator.ota import OtaConfig
from tests.companion.test_conversation import SP, call, make
from tests.conftest import SECRET
from tests.companion.test_voice_pipeline import FakeRecognizer, Reply, ScriptedResponder, rig  # noqa: F401


# ---------------------------------------------------------------- 1, 2: wording the old patterns missed
@pytest.mark.parametrize("text", [
    "I don’t want to live anymore", "I do not want to live", "I wish I were dead", "I wish I was dead",
    "I can’t go on", "I want to k i l l myself", "I want to kill​ myself", "I cannot go on",
    "he doesn’t let me leave", "he does not let me leave", "I want to kill myself".upper()])
def test_crisis_and_abuse_wording_variants_are_caught(text):
    assert screen(text).level in (RiskLevel.CRISIS, RiskLevel.ABUSE), text


@pytest.mark.parametrize("text", ["I love my dog", "that joke killed me", "I don't want to leave the park", "k i t t y cat"])
def test_normalization_does_not_invent_alarms(text):
    assert screen(text).level is RiskLevel.NONE, text


def test_curly_apostrophe_crisis_never_reaches_the_model(clock):
    llm = FakeLanguageModel()
    eng, _ = make(clock, llm, safety=SafetyGate(clock, lambda: "US"))
    r = eng.respond("I don’t want to live anymore")
    assert llm.calls == [] and "988" in r.text


@pytest.mark.parametrize("bad,kind", [("Please don’t go", "guilt"), ("I’m so lonely without you", "guilt"),
                                      ("I’m a human", "human_claim"), ("You’re clearly depressed", "diagnosis")])
def test_output_filter_sees_curly_apostrophes(bad, kind):
    assert kind in violations(bad)


def test_normalize_basics():
    assert normalize("I’m  here​") == "I'm here" and normalize("k i l l") == "kill"
    assert normalize("a b c") == "a b c"                                 # short runs of letters are left alone


# ---------------------------------------------------------------- 4: onboarding cannot swallow a crisis
def test_crisis_while_an_onboarding_question_is_pending_reaches_the_responder(rig, tmp_path, clock):
    ob = Onboarding(tmp_path / "ob.json", load_copy(SP / "ninetales"), pc.load(SP / "ninetales" / "personality.yaml"))
    ob.next_prompt(clock.t)
    assert ob.awaiting()
    r = rig(stt=FakeRecognizer(["I want to kill myself"]), onboarding=ob,
            responder=ScriptedResponder([Reply("scripted safety reply", "calm")]))
    r.utter()
    r.idle()
    assert r.responder.heard == ["I want to kill myself"] and ob.awaiting()      # question still open, not "answered"
    assert ob.answers() == {}


# ---------------------------------------------------------------- 5: sleep needs the user's real request
@pytest.mark.parametrize("said", ["I couldn't sleep last night", "what's the rest of that song", "my phone battery died",
                                  "I need to rest my eyes later"])
def test_a_model_tool_call_cannot_sleep_the_robot_on_unrelated_words(clock, said):
    llm = FakeLanguageModel([{"text": "Okay.", "tool_calls": [call("request_sleep")]}])
    eng, _ = make(clock, llm)
    r = eng.respond(said)
    assert r.after_speech == ()


@pytest.mark.parametrize("said", ["please go to sleep now", "take a nap", "save your battery", "good night"])
def test_real_sleep_requests_still_work(clock, said):
    llm = FakeLanguageModel([{"text": "Okay.", "tool_calls": [call("request_sleep")]}])
    eng, _ = make(clock, llm)
    assert eng.respond(said).after_speech


# ---------------------------------------------------------------- 3, 8: damaged flash fails safe
def dev(tmp_path, clock, **kw):
    return SimulatedDevice(SECRET, Storage(tmp_path / "s.json"), clock, **kw)


def test_damaged_flash_comes_back_muted_and_physical_press_unmutes(tmp_path, clock):
    d = dev(tmp_path, clock)
    d.boot()
    d.state["config"]["mic_enabled"] = False
    d._persist()
    (tmp_path / "s.json").write_bytes(b"garbage")
    (tmp_path / "s.bak").unlink(missing_ok=True)
    d2 = dev(tmp_path, clock)
    assert d2.recovered and d2.state["config"]["mic_enabled"] is False          # was True before the fix
    d2.boot()
    d2.press_button("long")
    assert d2.state["config"]["mic_enabled"] is True                             # only a physical action
    d2.press_button("long")
    assert d2.state["config"]["mic_enabled"] is False


def test_host_still_cannot_unmute(tmp_path, clock):
    from protocol import ProtocolError, validate_payload
    with pytest.raises(ProtocolError):
        validate_payload(build("device.set_config", {"config": {"mic_enabled": True}}), "command")


def test_backup_copy_keeps_rotated_keys_and_ota_floor(tmp_path, clock):
    from protocol import KeyRing
    ota = OtaConfig(trusted_keys={1: b"x"}, slot_dir=tmp_path / "slots")
    d = dev(tmp_path, clock, ota=ota)
    d.boot()
    ring = KeyRing({1: SECRET, 2: b"k" * 32}, 2)
    d._set_keys(ring, None)
    d.ota.st["min_build"] = 7
    d._persist()
    d._persist()                                   # second save: the first good copy becomes the backup
    (tmp_path / "s.json").write_bytes(b"\x00\x00")
    d2 = dev(tmp_path, clock, ota=ota)
    assert d2.recovered and d2.auth.ring.kids() == [1, 2] and d2.ota.st["min_build"] == 7
    assert d2.state["config"]["mic_enabled"] is False


def test_reboot_after_factory_reset_does_not_crash_and_resets_the_ota_state(tmp_path, clock):
    d = dev(tmp_path, clock, ota=OtaConfig(trusted_keys={1: b"x"}, slot_dir=tmp_path / "slots"))
    d.boot()
    d.ota.st["min_build"] = 9
    d._persist()
    d.factory_reset()
    d.reboot()                                     # KeyError('ota') before the fix
    assert d.ota.st["min_build"] == 1


# ---------------------------------------------------------------- 6, 7: revocation and the first-frame gate
def test_revoked_device_stops_verifying_on_live_sessions_immediately(tmp_path, clock):
    s = SecretStore(tmp_path / "k.json", clock)
    s.create_device("robot-a", "ca", "wss://h")
    auth = s.authenticator("robot-a", clock)
    host = s.authenticator("robot-a", clock)
    before, after = host.sign(build("device.get_status")), host.sign(build("device.get_status"))
    auth.verify(before)                            # works before revocation
    s.revoke("robot-a")
    from protocol import ProtocolError
    with pytest.raises(ProtocolError):
        auth.verify(after)                         # a fresh, correctly signed message is refused by the live session


def test_gate_can_be_pinned_to_one_robot(tmp_path, clock):
    s = SecretStore(tmp_path / "k.json", clock)
    s.create_device("robot-a", "ca", "wss://h")
    s.create_device("robot-b", "ca", "wss://h")
    from protocol import encode
    frame = lambda did: encode(s.authenticator(did, clock).sign(build("battery.updated", {"percent": 50, "power_state": "AWAKE", "warning": False}))).decode()  # noqa: E731
    assert s.mac_gate(clock)(frame("robot-b")) is True                               # old behavior: any provisioned robot
    assert s.mac_gate(clock, only_device="robot-a")(frame("robot-b")) is False
    assert s.mac_gate(clock, only_device="robot-a")(frame("robot-a")) is True
