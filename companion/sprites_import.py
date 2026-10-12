"""Builds a sprite set from still pictures of the character (front, two 45-degree views, two side views).

The stills carry no animation, so motion is made from them: gentle bobbing, hops, head turns between views, and small
overlay symbols (z, hearts, question mark, ...). Everything this produces is ordinary sprite data: open the result in the
editor and redraw any frame. If you export REAL animation frames from your art tool, pass them with `frames=` and they
replace the generated motion for that animation.

Needs Pillow (`pip install pillow`) only to read PNGs. Original artwork only: the caller is responsible for the right to
use the pictures (a generator's terms, a commissioned artist's licence). No franchise artwork."""
from __future__ import annotations

from pathlib import Path

from . import sprites as sp

VIEWS = ("front", "right45", "left45", "left", "right")
MAX_ART_COLORS = 56                    # leaves room for 4 greys (offline) and a few free slots for editing

GLYPHS = {
    "z": ["XXXXX", "...X.", "..X..", ".X...", "XXXXX"],
    "z_small": ["XXX", ".X.", "XXX"],
    "heart": [".X.X.", "XXXXX", "XXXXX", ".XXX.", "..X.."],
    "question": [".XXX.", "X...X", "...X.", "..X..", ".....", "..X.."],
    "exclaim": ["XX", "XX", "XX", "XX", "..", "XX"],
    "sparkle": ["..X..", "..X..", "XXXXX", "..X..", "..X.."],
    "wave": ["..X", ".X.", "X..", "X..", ".X.", "..X"],
    "anger": ["XX.XX", "X...X", ".....", "X...X", "XX.XX"],
    "tear": ["X", "X", "X"],
    "dot": ["XX", "XX"],
}


def read_png(path: str | Path):
    try:
        from PIL import Image
    except ImportError as e:
        raise sp.SpriteError("Pillow is needed to read PNG files: pip install pillow") from e
    im = Image.open(path).convert("RGBA")
    return im.size, list(getattr(im, "get_flattened_data", im.getdata)())


def _dist(a, b) -> int:
    return sum((x - y) ** 2 for x, y in zip(a, b))


def build_palette(images: list[list[tuple]], max_colors: int = MAX_ART_COLORS):
    """Colours used by the pictures (alpha > 0 counts as opaque). Too many? the closest pairs are merged, keeping the
    more common colour. Returns ([rgb, ...] without the transparent entry, {rgb: index starting at 1})."""
    counts: dict[tuple, int] = {}
    for px in images:
        for r, g, b, a in px:
            if a > 0:
                counts[(r, g, b)] = counts.get((r, g, b), 0) + 1
    remap = {c: c for c in counts}
    live = dict(counts)
    while len(live) > max_colors:
        cols = list(live)
        best = min(((_dist(cols[i], cols[j]), i, j) for i in range(len(cols)) for j in range(i + 1, len(cols))))
        _, i, j = best
        keep, drop = (cols[i], cols[j]) if live[cols[i]] >= live[cols[j]] else (cols[j], cols[i])
        live[keep] += live.pop(drop)
        for k, v in remap.items():
            if v == drop:
                remap[k] = keep
    ordered = sorted(live, key=lambda c: (sum(c), c))
    index = {c: i + 1 for i, c in enumerate(ordered)}
    return ordered, {c: index[remap[c]] for c in counts}


def rows_from(size, px, mapping) -> list[str]:
    w, h = size
    return ["".join(sp.ALPHABET[mapping[(r, g, b)]] if a > 0 else "0" for (r, g, b, a) in px[y * w:(y + 1) * w]) for y in range(h)]


# ---- motion made from stills -------------------------------------------------------------------------------------
def shift(rows: list[str], dx: int = 0, dy: int = 0) -> list[str]:
    w = len(rows[0])
    out = []
    for y in range(len(rows)):
        sy = y - dy
        if not 0 <= sy < len(rows):
            out.append("0" * w)
            continue
        r = rows[sy]
        out.append(("0" * dx + r)[:w] if dx > 0 else (r[-dx:] + "0" * -dx if dx < 0 else r))
    return out


def stamp(rows: list[str], glyph: str, x: int, y: int, ch: str) -> list[str]:
    rows = list(rows)
    for gy, line in enumerate(GLYPHS[glyph]):
        for gx, c in enumerate(line):
            px, py = x + gx, y + gy
            if c == "X" and 0 <= py < len(rows) and 0 <= px < len(rows[0]):
                rows[py] = rows[py][:px] + ch + rows[py][px + 1:]
    return rows


def pick_colors(palette: list[tuple]) -> dict[str, int]:
    """Palette indices (1-based) for the overlay symbols, chosen from the art's own colours so nothing clashes."""
    def idx(key):
        return palette.index(max(palette, key=key)) + 1
    return {"ink": idx(lambda c: -sum(c)), "cream": idx(lambda c: sum(c)),
            "gold": idx(lambda c: (c[0] + c[1]) / 2 - c[2] + min(c[0], c[1]) / 2),
            "coral": idx(lambda c: (c[0] - (c[1] + c[2]) / 2) + sum(c) / 6),
            "teal": idx(lambda c: (c[1] + c[2]) / 2 - c[0])}


def build_set(views: dict[str, str | Path], name: str = "default", screen: dict | None = None,
              frames: dict[str, list[str | Path]] | None = None, frame_ms: int = 120) -> dict:
    missing = [v for v in VIEWS if v not in views]
    if missing:
        raise sp.SpriteError(f"missing views: {missing}")
    loaded = {v: read_png(views[v]) for v in VIEWS}
    extra = {a: [read_png(p) for p in paths] for a, paths in (frames or {}).items()}
    sizes = {s for s, _ in loaded.values()} | {s for fs in extra.values() for s, _ in fs}
    if len(sizes) != 1:
        raise sp.SpriteError(f"all pictures must have the same size, found {sorted(sizes)}")
    (w, h), = sizes
    pals, mapping = build_palette([px for _, px in loaded.values()] + [px for fs in extra.values() for _, px in fs])
    R = {v: rows_from(loaded[v][0], loaded[v][1], mapping) for v in VIEWS}
    F, A, B, L, Rt = R["front"], R["right45"], R["left45"], R["left"], R["right"]
    col = pick_colors(pals)
    C = {k: sp.ALPHABET[i] for k, i in col.items()}
    palette = ["#00000000"] + [f"#{r:02x}{g:02x}{b:02x}" for r, g, b in pals]
    # four greys for the offline look
    greys = {}
    for i, (r, g, b) in enumerate(pals, start=1):
        lum = (299 * r + 587 * g + 114 * b) // 1000
        level = min(3, lum // 64)
        if level not in greys:
            greys[level] = len(palette)
            v = (40, 95, 150, 205)[level]
            palette.append(f"#{v:02x}{v:02x}{v:02x}")
        mapping_grey = greys[level]
        greys.setdefault(("map", i), mapping_grey)
    grey_of = {i: greys[("map", i)] for i in range(1, len(pals) + 1)}

    def greyed(rows):
        return ["".join("0" if c == "0" else sp.ALPHABET[grey_of[sp.ALPHABET.index(c)]] for c in r) for r in rows]

    ox, oy = w - 9, 1                                      # top-right corner for symbols
    fr = lambda rows, ms: {"ms": ms, "rows": rows}  # noqa: E731
    anims = {
        "idle": [fr(A, 900), fr(shift(A, 0, 1), 900)],
        "calm": [fr(A, 1300), fr(shift(A, 0, 1), 1300)],
        "listening": [fr(stamp(F, "wave", ox, oy + 2, C["teal"]), 450), fr(stamp(shift(F, 0, -1), "wave", ox + 2, oy + 2, C["teal"]), 450)],
        "thinking": [fr(stamp(B, "dot", ox, oy + 5, C["ink"]), 400),
                     fr(stamp(stamp(B, "dot", ox, oy + 5, C["ink"]), "dot", ox + 3, oy + 3, C["ink"]), 400),
                     fr(stamp(stamp(stamp(B, "dot", ox, oy + 5, C["ink"]), "dot", ox + 3, oy + 3, C["ink"]), "dot", ox + 6, oy, C["ink"]), 500)],
        "talking": [fr(F, 140), fr(shift(F, 0, -1), 140)],
        "eating": [fr(F, 180), fr(shift(F, 0, -1), 180), fr(F, 180), fr(shift(F, 1, 0), 180)],
        "happy": [fr(F, 170), fr(stamp(shift(F, 0, -3), "sparkle", ox, oy, C["gold"]), 170), fr(F, 170), fr(shift(F, 0, -3), 170)],
        "excited": [fr(shift(F, -1, 0), 110), fr(shift(F, 1, -3), 110), fr(shift(F, 1, 0), 110), fr(shift(F, -1, -3), 110)],
        "celebratory": [fr(stamp(shift(A, 0, -3), "sparkle", ox, oy, C["gold"]), 160), fr(A, 160),
                        fr(stamp(shift(B, 0, -3), "sparkle", 1, oy, C["gold"]), 160), fr(B, 160)],
        "playing": [fr(L, 220), fr(shift(L, 0, -2), 220), fr(Rt, 220), fr(shift(Rt, 0, -2), 220)],
        "curious": [fr(A, 700), fr(F, 500), fr(B, 700), fr(F, 500)],
        "confused": [fr(stamp(B, "question", ox, oy, C["ink"]), 500), fr(stamp(shift(B, 1, 0), "question", ox, oy, C["ink"]), 500)],
        "surprised": [fr(stamp(shift(F, 0, -2), "exclaim", ox + 3, oy, C["ink"]), 350), fr(stamp(F, "exclaim", ox + 3, oy, C["ink"]), 350)],
        "affectionate": [fr(stamp(F, "heart", ox, oy, C["coral"]), 500), fr(stamp(shift(F, 0, 1), "heart", ox, oy - 1, C["coral"]), 500)],
        "sad": [fr(stamp(shift(F, 0, 2), "tear", 17, 24, C["teal"]), 900), fr(shift(F, 0, 3), 900)],
        "angry": [fr(stamp(shift(F, -1, 0), "anger", ox, oy, C["ink"]), 120), fr(stamp(shift(F, 1, 0), "anger", ox, oy, C["ink"]), 120)],
        "sleepy": [fr(shift(A, 0, 2), 1000), fr(shift(A, 0, 3), 1000)],
        "sleeping": [fr(stamp(shift(A, 0, 3), "z_small", ox + 2, oy + 3, C["ink"]), 900), fr(stamp(shift(A, 0, 3), "z", ox, oy, C["ink"]), 900)],
        "waking": [fr(shift(A, 0, 3), 400), fr(shift(A, 0, 1), 400), fr(A, 500)],
        "reflective": [fr(L, 1300), fr(shift(L, 0, 1), 1300)],
        "offline": [fr(greyed(shift(A, 0, 2)), 1500)],
    }
    out = {a: {"loop": a != "waking", "frames": f} for a, f in anims.items()}
    for a, fs in extra.items():                           # real frames from the art tool replace the generated motion
        out[a] = {"loop": True, "frames": [fr(rows_from(size, px, mapping), frame_ms) for size, px in fs][:sp.MAX_FRAMES]}
    doc = {"version": 1, "name": name, "width": w, "height": h, "screen": screen or {"width": 240, "height": 240, "shape": "square"},
           "palette": palette, "animations": out}
    return sp.validate(doc)
