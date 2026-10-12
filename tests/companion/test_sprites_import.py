import pytest

PIL = pytest.importorskip("PIL")
from PIL import Image  # noqa: E402

from companion import sprites as sp  # noqa: E402
from companion import sprites_import as si  # noqa: E402
from tools import import_sprites  # noqa: E402


def make_png(path, color=(200, 120, 40), size=(16, 16), body=(4, 4, 12, 14), extra=None):
    im = Image.new("RGBA", size, (0, 0, 0, 0))
    for y in range(body[1], body[3]):
        for x in range(body[0], body[2]):
            im.putpixel((x, y), color + (255,))
    for (x, y), c in (extra or {}).items():
        im.putpixel((x, y), c + (255,))
    im.save(path)


@pytest.fixture
def views(tmp_path):
    out = {}
    for i, v in enumerate(si.VIEWS):
        p = tmp_path / f"{v}.png"
        make_png(p, extra={(5 + i, 5): (20, 20, 20), (6, 6): (250, 240, 220), (7, 7): (230, 90, 100), (8, 8): (40, 160, 150)})
        out[v] = p
    return out


def test_builds_a_valid_set_with_every_animation(views):
    doc = si.build_set(views)
    from protocol.schema_consts import ANIMATIONS
    assert set(doc["animations"]) == set(ANIMATIONS) and doc["width"] == doc["height"] == 16
    assert doc["palette"][0] == "#00000000" and len(doc["palette"]) <= 64


def test_the_original_pixels_survive_in_the_base_views(views):
    doc = si.build_set(views)
    rows = doc["animations"]["talking"]["frames"][0]["rows"]            # talking frame 0 is the unshifted front view
    assert rows[8][8] != "0" and rows[0] == "0" * 16                    # body kept, empty margin stays empty
    pal = doc["palette"][sp.ALPHABET.index(rows[8][4])]
    assert pal == "#c87828"                                             # (200,120,40) exactly


def test_motion_frames_differ_from_the_still(views):
    doc = si.build_set(views)
    f = doc["animations"]["idle"]["frames"]
    assert f[0]["rows"] != f[1]["rows"] and len(f) == 2
    assert doc["animations"]["waking"]["loop"] is False and doc["animations"]["idle"]["loop"] is True


def test_overlays_use_colours_from_the_art_not_new_ones(views):
    doc = si.build_set(views)
    art = {c for c in doc["palette"][1:-4]}
    for a in ("sleeping", "affectionate", "thinking", "happy"):
        used = {ch for fr in doc["animations"][a]["frames"] for r in fr["rows"] for ch in r} - {"0"}
        assert {doc["palette"][sp.ALPHABET.index(ch)] for ch in used} <= set(doc["palette"])
    assert len(art) >= 4


def test_offline_is_grey(views):
    doc = si.build_set(views)
    used = {ch for r in doc["animations"]["offline"]["frames"][0]["rows"] for ch in r} - {"0"}
    for ch in used:
        c = doc["palette"][sp.ALPHABET.index(ch)]
        assert c[1:3] == c[3:5] == c[5:7], c


def test_more_than_the_limit_of_colours_are_merged_keeping_common_ones(tmp_path):
    p = tmp_path / "many.png"
    im = Image.new("RGBA", (16, 16), (0, 0, 0, 0))
    for i in range(100):
        im.putpixel((i % 16, i // 16), (i * 2 % 256, (i * 5) % 256, (i * 11) % 256, 255))
    for x in range(16):
        im.putpixel((x, 10), (7, 7, 7, 255))                              # the most common colour must survive
    im.save(p)
    pals, mapping = si.build_palette([si.read_png(p)[1]], max_colors=20)
    assert len(pals) == 20 and (7, 7, 7) in pals and all(1 <= v <= 20 for v in mapping.values())


def test_missing_views_and_size_mismatch_are_clear_errors(views, tmp_path):
    v = dict(views)
    del v["left"]
    with pytest.raises(sp.SpriteError, match="missing views"):
        si.build_set(v)
    big = tmp_path / "big.png"
    make_png(big, size=(20, 20), body=(2, 2, 10, 10))
    v = dict(views)
    v["front"] = big
    with pytest.raises(sp.SpriteError, match="same size"):
        si.build_set(v)


def test_real_frames_replace_the_generated_motion(views, tmp_path):
    frames = []
    for i in range(3):
        p = tmp_path / f"idle_{i}.png"
        make_png(p, body=(4, 4 + i, 12, 14))
        frames.append(p)
    doc = si.build_set(views, frames={"idle": frames}, frame_ms=90)
    f = doc["animations"]["idle"]["frames"]
    assert len(f) == 3 and all(x["ms"] == 90 for x in f) and f[0]["rows"] != f[2]["rows"]


def test_helpers_shift_and_stamp_stay_inside_the_canvas():
    rows = ["0000", "0110", "0110", "0000"]
    assert si.shift(rows, 1, 1) == ["0000", "0000", "0011", "0011"]
    assert si.shift(rows, -3, -3)[0] == "0000" and all(len(r) == 4 for r in si.shift(rows, 9, 9))
    out = si.stamp(rows, "heart", 2, 2, "9")                                  # partly off the canvas: clipped, no error
    assert len(out) == 4 and all(len(r) == 4 for r in out)


def test_cli_roundtrip_and_errors(views, tmp_path, capsys):
    d = tmp_path / "src"
    d.mkdir()
    for v, p in views.items():
        (d / f"{v}.png").write_bytes(p.read_bytes())
    out = tmp_path / "out" / "set.json"
    assert import_sprites.main(["--dir", str(d), "--out", str(out)]) == 0
    sp.load(out)
    assert import_sprites.main(["--dir", str(tmp_path / "nope"), "--out", str(out)]) == 1
    assert import_sprites.main(["--dir", str(d), "--out", str(out), "--anim", "idle=" + str(tmp_path / "none_*.png")]) == 1


def test_the_committed_art_is_the_users_set_not_the_starter():
    from pathlib import Path
    root = Path(__file__).parents[2] / "assets" / "sprites"
    doc = sp.load(root / "default.json")
    assert doc["width"] == doc["height"] == 48 and len(doc["palette"]) > 16
    for v in si.VIEWS:
        assert (root / "source" / f"{v}.png").exists()


# ---------------------------------------------------------------- sprite-sheet exports and real animation frames
def make_sheet(tmp_path, cell=24, size=16, rot=8, anim=9, bad_meta=False):
    import json
    cols = 9
    sheet = Image.new("RGBA", (cols * cell, 2 * cell), (0, 0, 0, 0))
    pad = (cell - size) // 2
    for r, count in ((0, rot), (1, anim)):
        for i in range(count):
            for y in range(4, 12):
                for x in range(4, 12):
                    sheet.putpixel((i * cell + pad + x, r * cell + pad + y), (10 + r * 100 + i * 10, 90, 60, 255))
    png = tmp_path / "sheet.png"
    sheet.save(png)
    meta = {"character": {"name": "Idle", "size": {"width": size, "height": size}},
            "spritesheet": {"cell_size": {"width": cell, "height": cell}, "columns": cols, "rows": [
                {"row": 0, "type": "rotations", "frame_count": rot, "directions": list(("south south-east east north-east north north-west west south-west").split())[:rot]},
                {"row": 1, "type": "animation", "frame_count": anim}]}}
    if bad_meta:
        meta["spritesheet"]["rows"][1]["frame_count"] = 99
    mj = tmp_path / "sheet.json"
    mj.write_text(json.dumps(meta))
    return png, mj


def test_split_sheet_cuts_rotations_and_animation_frames_back_to_the_character_size(tmp_path):
    png, mj = make_sheet(tmp_path)
    parts = si.split_sheet(png, mj, tmp_path / "out")
    assert len(parts["rotations"]) == 8 and len(parts["animation"]) == 9
    assert (tmp_path / "out" / "south-east.png").exists() and (tmp_path / "out" / "idle_8.png").exists()
    im = Image.open(tmp_path / "out" / "idle_3.png").convert("RGBA")
    assert im.size == (16, 16) and im.getpixel((4, 4))[:3] == (140, 90, 60) and im.getpixel((0, 0))[3] == 0     # cropped, padding gone


def test_split_sheet_rejects_inconsistent_descriptions(tmp_path):
    png, mj = make_sheet(tmp_path, bad_meta=True)
    with pytest.raises(sp.SpriteError):
        si.split_sheet(png, mj, tmp_path / "out")
    (tmp_path / "bad.json").write_text("{}")
    with pytest.raises(sp.SpriteError, match="cannot read"):
        si.split_sheet(png, tmp_path / "bad.json", tmp_path / "o2")


def nine_frames(tmp_path):
    out = []
    for i in range(9):
        p = tmp_path / f"f{i}.png"
        make_png(p, color=(30 + i * 20, 100, 60), body=(4, 4, 12, 12), extra={(2 + i % 4, 2): (255, 0, 0)})
        out.append(p)
    return out


def test_nine_real_frames_drive_idle_and_the_expressions(views, tmp_path):
    f = nine_frames(tmp_path)
    doc = si.build_set(views, idle_frames=f, idle_ms=200)
    idle = doc["animations"]["idle"]["frames"]
    assert len(idle) == 9 and all(x["ms"] == 200 for x in idle) and len({tuple(x["rows"]) for x in idle}) == 9
    talk = doc["animations"]["talking"]["frames"]
    assert talk[0]["rows"] == idle[2]["rows"] and talk[1]["rows"] == idle[5]["rows"]            # open mouth, then closed
    assert doc["animations"]["happy"]["frames"][0]["rows"] == idle[8]["rows"]                   # the smile frame
    assert len(doc["animations"]["calm"]["frames"]) == 2                                          # poses without a real frame keep the generated motion
    sp.validate(doc)


def test_a_different_number_of_real_frames_only_replaces_idle(views, tmp_path):
    f = nine_frames(tmp_path)[:5]
    doc = si.build_set(views, idle_frames=f)
    assert len(doc["animations"]["idle"]["frames"]) == 5 and len(doc["animations"]["talking"]["frames"]) == 2


def test_cli_splits_a_sheet_then_builds_with_its_frames(views, tmp_path, capsys):
    from tools import import_sprites
    png, mj = make_sheet(tmp_path)
    d = tmp_path / "src"
    d.mkdir()
    for v, p in views.items():
        (d / f"{v}.png").write_bytes(p.read_bytes())
    assert import_sprites.main(["--dir", str(d), "--out", "-", "--split-sheet", str(png), str(mj)]) == 0
    assert (d / "sheet" / "idle_0.png").exists()
    out = tmp_path / "set.json"
    assert import_sprites.main(["--dir", str(d), "--out", str(out), "--idle-frames", str(d / "sheet" / "idle_*.png")]) == 0
    assert len(sp.load(out)["animations"]["idle"]["frames"]) == 9
    assert import_sprites.main(["--dir", str(d), "--out", str(out), "--idle-frames", str(d / "none_*.png")]) == 1


def test_the_committed_set_uses_the_real_idle_frames():
    from pathlib import Path
    root = Path(__file__).parents[2] / "assets" / "sprites"
    doc = sp.load(root / "default.json")
    assert len(doc["animations"]["idle"]["frames"]) == 9 and all(f["ms"] == 200 for f in doc["animations"]["idle"]["frames"])
    for n in ("idle_0", "idle_8", "south", "west"):
        assert (root / "source" / "sheet" / f"{n}.png").exists()
