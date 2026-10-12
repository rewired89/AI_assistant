import json
import urllib.error
import urllib.request

import pytest

from companion import sprites as sp
from companion import sprites_default
from protocol.schema_consts import ANIMATIONS
from tools.desk_view import EDITOR, VIEWER, ViewServer

STATE = {"anim": "happy", "power": "AWAKE", "battery": 90, "mic": True, "pipe": "idle", "stage": "BABY", "caption": "Hi!",
         "speaking": False, "notes": [], "level": 100, "threshold": 500, "heard": "", "heard_empty": False}


@pytest.fixture
def view(tmp_path):
    got = []
    v = ViewServer(lambda: STATE, lambda c, a: got.append((c, a)), port=0, sprites_path=tmp_path / "s" / "default.json")
    v.start()
    yield v, got
    v.stop()


def req(v, path, method="GET", body=None, headers=None, host=None):
    data = None if body is None else (body if isinstance(body, bytes) else json.dumps(body).encode())
    r = urllib.request.Request(v.url + path, data=data, method=method, headers=headers or {})
    if host:
        r.remove_header("Host")
        r.add_unredirected_header("Host", host)
    try:
        with urllib.request.urlopen(r, timeout=5) as resp:
            return resp.status, resp.read().decode(), dict(resp.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(), dict(e.headers)


def edit_headers(v, origin=True):
    h = {"Content-Type": "application/json", "X-NT-Edit": "1"}
    if origin:
        h["Origin"] = v.url
    return h


# ------------------------------------------------------------------ pages and state
def test_pages_state_and_default_sprites_are_served(view):
    v, _ = view
    s, body, h = req(v, "/")
    assert s == 200 and "<svg" in body and "no-store" in h["Cache-Control"] and "Content-Security-Policy" in h
    assert req(v, "/editor")[0] == 200 and "Pixel canvas" in req(v, "/editor")[1]
    assert json.loads(req(v, "/api/state")[1])["anim"] == "happy"
    doc = json.loads(req(v, "/api/sprites")[1])                  # nothing saved yet: the starter set
    sp.validate(doc)
    assert req(v, "/nope")[0] == 404


def test_every_protocol_animation_has_a_vector_face_and_a_starter_sprite():
    for name in ANIMATIONS:
        assert f"{name}:{{" in VIEWER, name
    assert set(sprites_default.make()["animations"]) == set(ANIMATIONS)


def test_other_hostnames_are_refused_dns_rebinding(view):
    v, _ = view
    assert req(v, "/api/state", host="evil.example")[0] == 403
    assert req(v, "/", host="evil.example:80")[0] == 403


# ------------------------------------------------------------------ commands
def test_commands_need_the_header_and_our_origin(view):
    v, got = view
    assert req(v, "/api/cmd", "POST", {"cmd": "press"}, {"Content-Type": "application/json"})[0] == 403          # no custom header
    assert req(v, "/api/cmd", "POST", {"cmd": "press"}, {**edit_headers(v), "Origin": "http://evil.example"})[0] == 403
    assert got == []
    assert req(v, "/api/cmd", "POST", {"cmd": "press"}, edit_headers(v))[0] == 200
    assert req(v, "/api/cmd", "POST", {"cmd": "battery", "arg": 15}, edit_headers(v))[0] == 200
    assert req(v, "/api/cmd", "POST", {"cmd": "mute"}, edit_headers(v, origin=False))[0] == 200                   # curl-style, no Origin
    assert got == [("press", None), ("battery", 15), ("mute", None)]


@pytest.mark.parametrize("bad", [b"not json", b"{}", {"cmd": "format_disk"}, {"cmd": "battery", "arg": 500}, {"cmd": "battery", "arg": True},
                                 {"cmd": "battery", "arg": "5"}, [1, 2]])
def test_junk_commands_are_rejected(view, bad):
    v, got = view
    assert req(v, "/api/cmd", "POST", bad, edit_headers(v))[0] == 400 and got == []


# ------------------------------------------------------------------ sprites API
def test_save_roundtrip_bumps_the_revision_and_keeps_a_backup(view, tmp_path):
    v, _ = view
    doc = sprites_default.make()
    doc["animations"]["idle"]["frames"][0]["rows"][5] = "1" * 64
    assert req(v, "/api/sprites", "PUT", doc, edit_headers(v))[0] == 200
    assert json.loads(req(v, "/api/state")[1])["rev"] == 1
    assert json.loads(req(v, "/api/sprites")[1])["animations"]["idle"]["frames"][0]["rows"][5] == "1" * 64
    doc["name"] = "second"
    assert req(v, "/api/sprites", "PUT", doc, edit_headers(v))[0] == 200
    assert (tmp_path / "s" / "default.bak").exists() and json.loads((tmp_path / "s" / "default.bak").read_text())["name"] == "default"


def test_bad_or_hostile_sprite_uploads_are_rejected_and_nothing_is_written(view, tmp_path):
    v, _ = view
    good = sprites_default.make()
    cases = [{"version": 2}, {**good, "name": "../../etc/passwd"}, {**good, "width": 9999},
             {**good, "palette": ["red"]}, {**good, "animations": {"not_an_animation": good["animations"]["idle"]}},
             {**good, "extra": 1}]
    for bad in cases:
        status, text, _ = req(v, "/api/sprites", "PUT", bad, edit_headers(v))
        assert status == 400 and "error" in json.loads(text), bad
    assert req(v, "/api/sprites", "PUT", b"garbage", edit_headers(v))[0] == 400
    assert not (tmp_path / "s" / "default.json").exists()


def test_sprite_writes_need_the_header_and_origin_and_a_sane_size(view, tmp_path):
    v, _ = view
    doc = sprites_default.make()
    assert req(v, "/api/sprites", "PUT", doc, {"Content-Type": "application/json"})[0] == 403
    assert req(v, "/api/sprites", "PUT", doc, {**edit_headers(v), "Origin": "http://evil.example"})[0] == 403
    try:
        status = req(v, "/api/sprites", "PUT", b"x" * (sp.MAX_BYTES + 10_000), edit_headers(v))[0]
    except urllib.error.URLError:
        status = 413                                              # the server refused before reading the body and hung up
    assert status == 413
    assert not (tmp_path / "s" / "default.json").exists()


def test_a_damaged_saved_file_falls_back_to_the_starter_set(view, tmp_path):
    v, _ = view
    (tmp_path / "s").mkdir()
    (tmp_path / "s" / "default.json").write_text("{broken")
    sp.validate(json.loads(req(v, "/api/sprites")[1]))
