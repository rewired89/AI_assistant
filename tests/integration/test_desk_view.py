import asyncio
import json
import urllib.request

import pytest
from websockets.asyncio.client import connect
from websockets.exceptions import InvalidStatus

from tools.desk_view import PAGE, ViewServer
from companion.speech.pipeline import PAUSE_MARK  # noqa: F401  (import check only)


@pytest.fixture
def view():
    got = []
    state = {"anim": "happy", "power": "AWAKE", "battery": 90, "mic": True, "pipe": "idle", "stage": "BABY", "caption": "Hi!",
             "speaking": False, "notes": []}
    v = ViewServer(lambda: state, lambda c, a: got.append((c, a)), port=0, interval_s=0.05)
    v.start()
    yield v, got
    v.stop()


def get(url):
    with urllib.request.urlopen(url, timeout=5) as r:
        return r.status, dict(r.headers), r.read().decode()


def test_page_is_served_with_the_face_and_the_right_port(view):
    v, _ = view
    status, headers, body = get(v.url + "/")
    assert status == 200 and "<svg" in body and f"ws://localhost:{v.port}/ws" in body
    assert headers["Cache-Control"] == "no-store" and "text/html" in headers["Content-Type"]
    with pytest.raises(urllib.error.HTTPError) as e:
        get(v.url + "/secret")
    assert e.value.code == 404


def test_every_protocol_animation_has_a_face():
    from protocol.schema_consts import ANIMATIONS
    for name in ANIMATIONS:
        assert f"{name}:{{" in PAGE, name


async def talk(v, origin, send=None):
    async with connect(f"ws://localhost:{v.port}/ws", origin=origin, proxy=None) as ws:
        first = json.loads(await asyncio.wait_for(ws.recv(), 3))
        for m in send or []:
            await ws.send(m if isinstance(m, str) else json.dumps(m))
        await asyncio.sleep(0.3)
        return first


def test_state_streams_and_commands_arrive(view):
    v, got = view
    first = asyncio.run(talk(v, f"http://localhost:{v.port}",
                             [{"cmd": "press"}, {"cmd": "battery", "arg": 15}, {"cmd": "mute"}]))
    assert first["anim"] == "happy" and first["caption"] == "Hi!"
    assert got == [("press", None), ("battery", 15), ("mute", None)]


def test_junk_and_out_of_range_commands_are_ignored(view):
    v, got = view
    asyncio.run(talk(v, f"http://localhost:{v.port}", ["not json", "{}", {"cmd": "format_disk"}, {"cmd": "battery", "arg": 500},
                                                       {"cmd": "battery", "arg": True}, {"cmd": "battery", "arg": "5"}, [1, 2]]))
    assert got == []


def test_other_web_pages_cannot_drive_it(view):
    v, got = view
    with pytest.raises(InvalidStatus):
        asyncio.run(talk(v, "http://evil.example", [{"cmd": "press"}]))
    assert got == []
