"""Puts the owner's sleeping-pose sprite sheet into the adult set (assets/sprites/default.json).

    python -m tools.import_sleep                      # uses assets/sprites/source_sleep/spritesheet.png
    python -m tools.import_sleep --png sheet.png

The sheet is 8 rotations in a row of 48x48 cells; the south (front) cell is used. Only two clips are replaced, everything
else in the set (and any pixel edits) stays as it is:
  sleeping: her sleeping pose in colour, breathing (one pixel up and down), a small z on the second frame.
  offline:  the same pose in black and greys only, so it is clear she is not connected.
Colours are matched to the colours already in the set's palette (the same character), never added."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from companion import sprites as sp
from companion import sprites_import as si

ROOT = Path(__file__).resolve().parent.parent
SPRITES = ROOT / "assets" / "sprites"


def _rgb(c: str) -> tuple[int, int, int]:
    return int(c[1:3], 16), int(c[3:5], 16), int(c[5:7], 16)


def _near(c, cands):
    return min(cands, key=lambda i: sum((a - b) ** 2 for a, b in zip(c, cands[i])))


def build(doc: dict, sheet: Path, cell: int = 48) -> dict:
    from PIL import Image
    im = Image.open(sheet).convert("RGBA")
    if im.height != cell or im.width % cell:
        raise sp.SpriteError(f"expected a row of {cell}x{cell} cells, got {im.size}")
    south = im.crop((0, 0, cell, cell))
    if (doc["width"], doc["height"]) != (cell, cell):
        raise sp.SpriteError("set size differs from the sheet cell size")
    pal = doc["palette"]
    colour = {i: _rgb(c) for i, c in enumerate(pal) if i > 0 and len(c) == 7 and c != "#000000"}
    grey_ids = {i: _rgb(pal[i]) for i in (1, 57, 58, 59, 60)}
    px = list(south.getdata())
    rows, grey = [], []
    for y in range(cell):
        r, g = "", ""
        for x in range(cell):
            R, G, B, A = px[y * cell + x]
            if A < 128:
                r += "0"; g += "0"
                continue
            r += sp.ALPHABET[_near((R, G, B), {1: (0, 0, 0), **colour})]
            lum = round(0.299 * R + 0.587 * G + 0.114 * B)
            g += sp.ALPHABET[_near((lum, lum, lum), grey_ids)]
        rows.append(r); grey.append(g)
    z = si.pick_colors([_rgb(pal[i]) for i in range(1, 57)])
    breathe = si.shift(rows, 0, 1)
    breathe = si.stamp(breathe, "z_small", 36, 6, sp.ALPHABET[z["cream"]])
    out = dict(doc)
    out["animations"] = dict(doc["animations"])
    out["animations"]["sleeping"] = {"loop": True, "frames": [{"ms": 900, "rows": rows}, {"ms": 900, "rows": breathe}]}
    out["animations"]["offline"] = {"loop": True, "frames": [{"ms": 1000, "rows": grey}]}
    return sp.validate(out)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--png", default=str(SPRITES / "source_sleep" / "spritesheet.png"))
    a = ap.parse_args()
    path = SPRITES / "default.json"
    sp.save(path, build(sp.load(path), Path(a.png)))
    print(f"updated {path.name}: sleeping (colour, breathing) and offline (greys)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
