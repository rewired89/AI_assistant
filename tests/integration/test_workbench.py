import json
import shutil
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from companion import sprites as sp
from companion import workbench as wb

SPR = Path(__file__).parents[2] / "assets" / "sprites"


@pytest.fixture
def sprites(tmp_path):
    d = tmp_path / "sprites"
    shutil.copytree(SPR, d, ignore=shutil.ignore_patterns("*.bak", "*.gif"))
    return d


def test_the_committed_assets_are_clean():
    assert wb.check(SPR) == []


def test_every_protocol_animation_exists_in_the_default_set_and_nothing_is_hidden():
    c = wb.catalog(SPR)
    default = next(s for s in c["sets"] if s["name"] == "default")
    have = {a["id"] for a in default["animations"] if a["frames"]}
    assert sp.ANIMATIONS <= have
    assert {a["id"]: a["status"] for a in default["animations"]}["tail_wag"] == "missing"        # no art: said so, not faked
    assert all(a["frames"] == 0 for a in default["animations"] if a["status"] == "missing")


def test_missing_set_file_and_missing_source_picture_are_reported(sprites):
    (sprites / "baby.json").unlink()
    (sprites / "source" / "front.png").unlink()
    p = wb.check(sprites)
    assert any("baby.json is missing" in x for x in p) and any("source/front.png is missing" in x for x in p)


def test_a_claim_without_frames_and_art_without_a_claim_are_both_caught(sprites):
    doc = json.loads((sprites / "default.json").read_text())
    del doc["animations"]["thinking"]
    (sprites / "default.json").write_text(json.dumps(doc))
    assert any("'thinking'" in x and "no frames" in x for x in wb.check(sprites))
    m = json.loads((sprites / "manifest.json").read_text())
    m["animations"]["walk"]["status"] = "missing"
    (sprites / "manifest.json").write_text(json.dumps(m))
    assert any("manifest says 'walk' is missing but frames exist" in x for x in wb.check(sprites))


def test_invalid_frame_references_are_caught(sprites):
    doc = json.loads((sprites / "baby.json").read_text())
    doc["animations"]["happy"]["frames"][0]["rows"][0] = "Z" * doc["width"]            # palette index beyond the palette
    (sprites / "baby.json").write_text(json.dumps(doc))
    assert any("set baby" in x for x in wb.check(sprites))
    doc["animations"]["happy"]["frames"][0]["rows"] = doc["animations"]["happy"]["frames"][0]["rows"][:-1]   # a row short
    (sprites / "baby.json").write_text(json.dumps(doc))
    assert any("set baby" in x and "rows" in x for x in wb.check(sprites))


@pytest.mark.parametrize("edit", [
    lambda m: m["animations"].__setitem__("flying", {"category": "idle", "label": "x", "kind": "workbench", "status": "composed"}),
    lambda m: m["animations"]["idle"].__setitem__("category", "nonsense"),
    lambda m: m["animations"]["idle"].__setitem__("kind", "workbench"),
    lambda m: m["animations"]["walk"].__setitem__("kind", "protocol"),
    lambda m: m["animations"]["idle"].__setitem__("real_in", ["ghost"]),
    lambda m: m.__setitem__("version", 2)])
def test_malformed_manifests_are_rejected(sprites, edit):
    m = json.loads((sprites / "manifest.json").read_text())
    edit(m)
    (sprites / "manifest.json").write_text(json.dumps(m))
    assert wb.check(sprites)[0].startswith("manifest:")


def test_unparseable_sprite_file_is_a_problem_not_a_crash(sprites):
    (sprites / "egg.json").write_text("{nope")
    assert any("set egg" in x for x in wb.check(sprites))
    assert any(s["name"] == "egg" and s["error"] for s in wb.catalog(sprites, wb.load_manifest(sprites / "manifest.json"))["sets"])


def test_replacing_a_source_picture_rebuilds_the_set(sprites):
    from PIL import Image
    api = wb.WorkbenchApi(sprites)
    first = api.refresh()["fingerprint"]
    assert api.refresh()["rebuilt"] == []
    src = sprites / "source_baby" / "front.png"
    im = Image.open(src).convert("RGBA")
    px = im.load()
    for x in range(im.width):
        for y in range(im.height):
            if px[x, y][3]:
                px[x, y] = (10, 200, 10, 255)
    time.sleep(0.02)
    im.save(src)
    r = api.refresh()
    assert r["rebuilt"] and r["fingerprint"] != first and not r["errors"]
    assert any("#0ac80a" in c for c in sp.load(sprites / "baby.json")["palette"])


def test_a_broken_picture_does_not_take_the_workbench_down(sprites):
    (sprites / "source_baby" / "front.png").write_bytes(b"not a png")
    r = wb.WorkbenchApi(sprites).refresh()
    assert r["errors"] and "baby" in r["errors"][0]


def test_simulator_uses_the_real_engine_and_labels_everything():
    sim = wb.Simulator("ninetales", "happy")
    r = sim.fire("wake")
    assert r["simulated"] and r["steps"][0]["animation"] == "waking" and r["steps"][1]["dialogue"]
    msg = sim.fire("message")
    assert [s["animation"] for s in msg["steps"]] == ["listening", "thinking", "talking"]
    assert all("[sample" in s["dialogue"] for s in msg["steps"] if s.get("dialogue"))          # never passed off as AI output
    low = sim.fire("battery_low")
    assert [s["animation"] for s in low["steps"]] == ["sleepy", "low_battery"] and low["notes"]
    sim.fire("sleep")
    assert sim.fire("touch")["steps"] == []                                                       # asleep: the real engine ignores touch
    with pytest.raises(ValueError):
        sim.fire("explode")
    with pytest.raises(ValueError):
        sim.fire("emotion:../../etc")


def test_personality_and_mood_change_what_comes_out():
    assert wb.Simulator("ninetales", "sleepy").state.mood == "sleepy"
    assert wb.Simulator("ninetales", "happy").state.mood == "happy"
    assert set(wb.Simulator.profiles()) >= {"ninetales", "cubone"}


def test_the_extra_clips_are_built_from_real_poses_and_stay_the_same_size():
    d = sp.load(SPR / "default.json")
    for n in ("blink", "look_left", "look_right", "walk", "low_battery"):
        assert all(len(f["rows"]) == d["height"] and all(len(r) == d["width"] for r in f["rows"]) for f in d["animations"][n]["frames"])
    left, right = d["animations"]["look_left"]["frames"], d["animations"]["look_right"]["frames"]
    assert left[2]["rows"] != right[2]["rows"]                                                    # different poses, not one flipped copy


def _server(sprites):
    from tools.desk_view import ViewServer
    v = ViewServer(lambda: {"anim": "idle"}, lambda c, x: None, port=0, sprites_path=sprites / "default.json")
    v.start()
    return v


def _req(v, path, body=None, headers=None):
    h = {"Host": f"localhost:{v.port}", **(headers or {})}
    req = urllib.request.Request(v.url + path, data=json.dumps(body).encode() if body is not None else None, headers=h)
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def test_the_page_and_its_api_are_served_and_writes_need_the_header(sprites):
    v = _server(sprites)
    try:
        st, body = _req(v, "/workbench")
        assert st == 200 and b"Sprite Workbench" in body
        st, body = _req(v, "/api/catalog")
        cat = json.loads(body)
        assert st == 200 and {s["name"] for s in cat["sets"]} == {"default", "baby", "egg"} and cat["problems"] == []
        assert _req(v, "/api/sprites?set=default")[0] == 200
        assert _req(v, "/api/sprites?set=../../etc/passwd")[0] == 404
        ok = {"X-NT-Edit": "1"}
        assert _req(v, "/api/simulate", {"event": "wake"})[0] == 403                                  # no header: refused
        st, body = _req(v, "/api/simulate", {"event": "wake", "mood": "happy"}, ok)
        assert st == 200 and json.loads(body)["simulated"] is True
        assert _req(v, "/api/simulate", {"event": "explode"}, ok)[0] == 400
        assert _req(v, "/api/refresh", {}, ok)[0] == 200
        assert _req(v, "/api/catalog", headers={"Host": "evil.example"})[0] == 403                    # DNS-rebinding guard
    finally:
        v.stop()


def test_device_export_round_trips_every_set_exactly():
    from tools import export_sprites as ex
    for name in ("default", "baby", "egg"):
        doc = sp.load(SPR / f"{name}.json")
        back = ex.load(ex.export(doc))
        assert back["animations"] == {k: {"loop": v.get("loop", True), "frames": v["frames"]} for k, v in doc["animations"].items()}
        assert back["palette"] == [c.lower() for c in doc["palette"]]


def test_export_refuses_broken_input_and_truncated_files():
    from tools import export_sprites as ex
    with pytest.raises(sp.SpriteError):
        ex.export({"version": 1})
    good = ex.export(sp.load(SPR / "egg.json"))
    with pytest.raises(sp.SpriteError):
        ex.load(good + b"x")                                  # trailing junk is refused
    with pytest.raises(sp.SpriteError):
        ex.load(b"XXXX" + good[4:])
