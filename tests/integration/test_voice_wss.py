"""One complete voice turn over real TLS: button -> capture -> STT -> reply -> TTS -> speaker."""
import time

from companion.application.clock import SystemClock
from companion.speech import Reply, State, VoicePipeline
from companion.speech.fakes import FakeRecognizer, FakeSynthesizer, silence, tone
from tests.integration.test_wss import client_for, host, make_link, pki, wait_for  # noqa: F401

CLOCK = SystemClock()


class Echo:
    def __init__(self):
        self.heard = []

    def respond(self, utterance):
        self.heard.append(utterance)
        return Reply("Hello there. Nice to hear you.", "happy")


def test_voice_turn_over_wss(host, make_link):
    dev, link = make_link(host.port)
    assert wait_for(lambda: host.connected)
    client = client_for(host)
    stt, tts, responder = FakeRecognizer(["hello fox"]), FakeSynthesizer(), Echo()
    pipe = VoicePipeline(client, stt, tts, responder, CLOCK)
    try:
        link.inject(lambda: dev.press_button("single"))
        assert wait_for(lambda: (pipe.poll() or True) and pipe.state is State.LISTENING)
        link.call(lambda: dev.feed_mic(tone(0.6)))
        link.call(lambda: dev.feed_mic(silence(1.3)))
        deadline = time.time() + 20
        while time.time() < deadline and not (pipe.state is State.IDLE and pipe.stats["turns"] == 1 and tts.calls
                                              and dev.playback is None and len(client.playback_results) == 2):
            pipe.poll()
            time.sleep(0.01)
        assert responder.heard == ["hello fox"]
        assert [t for t, _ in tts.calls] == ["Hello there.", "Nice to hear you."]
        assert all(r["result"] == "completed" and r["underruns"] == 0 for r in client.playback_results.values())
        assert pipe.state is State.IDLE and client.captures == {} and dev.audio_rejected == 0
    finally:
        pipe.close()
