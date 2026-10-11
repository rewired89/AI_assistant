"""The desk robot wiring, with fake audio devices and fake engines (no sound card, no model)."""
import queue
import threading
import time

import pytest
import yaml

from companion.conversation import FakeLanguageModel
from companion.speech.fakes import FakeRecognizer, FakeSynthesizer, tone
from tools import desk_robot


class FakeMic:
    """Silence until told to talk, then a burst, then silence again (like a person)."""
    def __init__(self):
        self.talk_until = 0.0
        self.closed = False

    def read(self):
        time.sleep(0.1)
        return tone(0.1, 300, 6000) if time.time() < self.talk_until else bytes(3200)


class FakeSpeaker:
    def __init__(self):
        self.bytes = 0

    def write(self, pcm):
        self.bytes += len(pcm)


def wait(cond, t=20):
    end = time.time() + t
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.05)
    return False


def test_init_creates_a_private_working_folder(tmp_path):
    p = desk_robot.init(tmp_path / "d", "http://192.168.1.42:11434", "llama3.2:3b", None, port=18443)
    cfg = yaml.safe_load(p.read_text())
    assert cfg["llm"] == {"engine": "ollama", "url": "http://192.168.1.42:11434", "model": "llama3.2:3b"}
    for f in ("keys.json", "flash.json", "tls/ca.pem", "tls/server.key"):
        assert (tmp_path / "d" / f).exists(), f
    with pytest.raises(FileExistsError):
        desk_robot.init(tmp_path / "d", "http://x", "m", None)


def test_press_talk_and_hear_an_answer_through_the_speaker(tmp_path):
    import socket
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    d = tmp_path / "d"
    desk_robot.init(d, "http://192.168.1.42:11434", "m", None, port=port)
    mic, spk, keys, stop = FakeMic(), FakeSpeaker(), queue.Queue(), threading.Event()
    stt, tts = FakeRecognizer(["Sam", "Kit", "hello there"]), FakeSynthesizer()
    t = threading.Thread(target=desk_robot.run, args=(d,), daemon=True,
                         kwargs=dict(mic=mic, speaker=spk, keys=keys, stop=stop, out=lambda *_: None,
                                     stt=stt, tts=tts, llm=FakeLanguageModel(default="Hello to you too.")))
    t.start()
    try:
        assert wait(lambda: spk.bytes > 0)                      # she speaks first (onboarding question), no button needed
        first = spk.bytes
        assert wait(lambda: len(tts.calls) >= 1)
        last, since = spk.bytes, time.time()
        while time.time() - since < 2.0:                         # wait until she has finished talking
            time.sleep(0.2)
            if spk.bytes != last:
                last, since = spk.bytes, time.time()
        first = spk.bytes
        keys.put("")                                             # ENTER: the button
        time.sleep(1.0)
        mic.talk_until = time.time() + 0.8
        assert wait(lambda: stt.calls, 15)                       # the robot heard audio from the "microphone"
        assert wait(lambda: spk.bytes > first, 20)
    finally:
        keys.put("q")
        t.join(15)
    assert not t.is_alive()
