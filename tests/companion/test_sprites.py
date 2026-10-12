import copy

import pytest

from companion import sprites as sp
from companion import sprites_default


@pytest.fixture
def doc():
    return sprites_default.make()


def test_starter_set_is_valid_and_fits_a_16_colour_palette(doc):
    sp.validate(doc)
    assert len(doc["palette"]) <= 16 and doc["width"] == doc["height"] == 64      # the generated starter set stays hex-only
    for name, a in doc["animations"].items():
        assert a["frames"], name


def test_starter_art_is_not_blank_and_expressions_differ(doc):
    def lit(name):
        return sum(ch != "0" for r in doc["animations"][name]["frames"][0]["rows"] for ch in r)
    assert all(lit(n) > 600 for n in doc["animations"])
    assert doc["animations"]["happy"]["frames"][0]["rows"] != doc["animations"]["sad"]["frames"][0]["rows"]
    assert len(doc["animations"]["talking"]["frames"]) == 2 and len(doc["animations"]["idle"]["frames"]) == 2   # mouth toggle, blink


def test_starter_art_is_left_right_symmetric_for_a_neutral_face(doc):
    rows = doc["animations"]["calm"]["frames"][0]["rows"]
    diff = sum(a != b for r in rows for a, b in zip(r, r[::-1]))
    assert diff == 0                                          # the face is built symmetric; only extras (z, dots, hearts) are not


@pytest.mark.parametrize("mutate,msg", [
    (lambda d: d.update(version=2), "version"), (lambda d: d.update(name="a/b"), "name"), (lambda d: d.update(width=7), "width"),
    (lambda d: d.update(width=True), "width"), (lambda d: d.update(palette=[]), "palette"), (lambda d: d.update(palette=["#12345"]), "palette"),
    (lambda d: d.update(palette=["#000000"] * 65), "palette"), (lambda d: d.update(extra=1), "unknown keys"),
    (lambda d: d.update(screen={"width": 10, "height": 240}), "screen.width"), (lambda d: d.update(screen={"width": 240, "height": 240, "shape": "hex"}), "shape"),
    (lambda d: d.update(animations={}), "animations"), (lambda d: d["animations"].update(zzz=d["animations"]["idle"]), "unknown animation"),
    (lambda d: d["animations"]["idle"].update(frames=[]), "frames"),
    (lambda d: d["animations"]["idle"].update(frames=d["animations"]["idle"]["frames"] * 7), "frames"),
    (lambda d: d["animations"]["idle"]["frames"][0].update(ms=1), "ms"),
    (lambda d: d["animations"]["idle"]["frames"][0].update(rows=["0" * 64] * 63), "exactly"),
    (lambda d: d["animations"]["idle"]["frames"][0]["rows"].__setitem__(0, "0" * 63), "every row"),
    (lambda d: d["animations"]["idle"]["frames"][0]["rows"].__setitem__(0, "z" * 64), "digits"),
    (lambda d: d["animations"]["idle"]["frames"][0]["rows"].__setitem__(0, "f" * 64), "digits"),          # index beyond the palette
    (lambda d: d["animations"]["idle"].update(loop="yes"), "animation entry"),
])
def test_validation_rejects_bad_files(doc, mutate, msg):
    d = copy.deepcopy(doc)
    mutate(d)
    with pytest.raises(sp.SpriteError, match=msg):
        sp.validate(d)


def test_not_a_dict_and_missing_keys():
    for bad in (None, [], "x", 5):
        with pytest.raises(sp.SpriteError):
            sp.validate(bad)


def test_save_is_atomic_keeps_a_backup_and_load_roundtrips(tmp_path, doc):
    p = tmp_path / "a" / "set.json"
    sp.save(p, doc)
    assert sp.load(p) == doc and not p.with_suffix(".tmp").exists()
    doc2 = copy.deepcopy(doc); doc2["name"] = "two"
    sp.save(p, doc2)
    assert sp.load(p)["name"] == "two" and sp.load(p.with_suffix(".bak"))["name"] == "default"
    bad = copy.deepcopy(doc); bad["width"] = 3
    with pytest.raises(sp.SpriteError):
        sp.save(p, bad)
    assert sp.load(p)["name"] == "two"                          # a rejected save leaves the good file alone


def test_load_errors_are_spriteerrors(tmp_path):
    (tmp_path / "x.json").write_text("{nope")
    with pytest.raises(sp.SpriteError):
        sp.load(tmp_path / "x.json")
    with pytest.raises(sp.SpriteError):
        sp.load(tmp_path / "missing.json")


def test_the_committed_default_set_is_valid():
    from pathlib import Path
    p = Path(__file__).parents[2] / "assets" / "sprites" / "default.json"
    assert p.exists()
    sp.load(p)


def test_palettes_up_to_64_colours_work_and_the_first_16_stay_hex(doc):
    d = copy.deepcopy(doc)
    d["palette"] = [f"#{i:02x}{i:02x}{i:02x}" for i in range(64)]
    row = sp.ALPHABET[:48] + sp.ALPHABET[:16]
    d["animations"] = {"idle": {"loop": True, "frames": [{"ms": 100, "rows": [row] * 64}]}}
    sp.validate(d)
    assert sp.ALPHABET[:16] == "0123456789abcdef"
    assert sp.pixel(d, "idle", 0, 63, 0) == 15 and sp.pixel(d, "idle", 0, 47, 0) == 47
    d["animations"]["idle"]["frames"][0]["rows"][0] = "-" * 64                  # index 63: fine with 64 colours
    sp.validate(d)
    d["palette"] = d["palette"][:60]
    with pytest.raises(sp.SpriteError, match="digits"):
        sp.validate(d)                                                          # but not beyond the palette
