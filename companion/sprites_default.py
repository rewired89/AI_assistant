"""Draws the starter sprite set procedurally (original fox-like face, 64x64, 12 colours) so there is something to edit.
It is a starting point for the pixel editor, not final art: change it in the editor and save."""
from __future__ import annotations

import math

from protocol.schema_consts import ANIMATIONS

W = H = 64
PALETTE = ["#00000000", "#f2b46d", "#7a4a2b", "#fff4e3", "#2b2320", "#ffffff", "#ff8f8f", "#8a2f2f", "#e89a4c", "#6aa7ff",
           "#c9b9a6", "#fbd9a8"]
T, FUR, OUT, CREAM, INK, WHITE, BLUSH, MOUTH, TAIL, BLUE, GREY, LIGHT = range(12)

# animation -> (eyes, mouth, brows, blushes, extra, loop)
SPEC = {
    "idle": ("open", "smile", "n", 0, None), "happy": ("happy", "grin", "up", 1, None), "excited": ("wide", "open", "up", 1, None),
    "celebratory": ("happy", "open", "up", 1, "sparkle"), "affectionate": ("happy", "smile", "sad", 1, "heart"),
    "calm": ("half", "smile", "n", 0, None), "sleepy": ("half", "flat", "sad", 0, None), "sleeping": ("closed", "flat", "n", 0, "zzz"),
    "waking": ("half", "o", "n", 0, None), "sad": ("sad", "frown", "sad", 0, "tear"), "angry": ("angry", "frown", "angry", 0, None),
    "confused": ("open", "flat", "sad", 0, "question"), "curious": ("wide", "o", "up", 0, None), "surprised": ("wide", "o", "up", 0, None),
    "listening": ("wide", "smile", "up", 0, "waves"), "thinking": ("up", "flat", "n", 0, "dots"), "talking": ("open", "talk", "n", 0, None),
    "eating": ("happy", "open", "n", 0, None), "playing": ("wide", "grin", "up", 1, "sparkle"), "reflective": ("half", "smile", "sad", 0, None),
    "offline": ("half", "flat", "sad", 0, "grey"),
}


class Canvas:
    def __init__(self):
        self.p = [[T] * W for _ in range(H)]

    def put(self, x, y, c):
        if 0 <= x < W and 0 <= y < H:
            self.p[y][x] = c

    def ellipse(self, cx, cy, rx, ry, c):
        for y in range(int(cy - ry) - 1, int(cy + ry) + 2):
            for x in range(int(cx - rx) - 1, int(cx + rx) + 2):
                if ((x + .5 - cx) / rx) ** 2 + ((y + .5 - cy) / ry) ** 2 <= 1:
                    self.put(x, y, c)

    def poly(self, pts, c):
        for y in range(H):
            for x in range(W):
                inside, j = False, len(pts) - 1
                for i in range(len(pts)):
                    xi, yi, xj, yj = *pts[i], *pts[j]
                    if (yi > y + .5) != (yj > y + .5) and (x + .5) < (xj - xi) * (y + .5 - yi) / (yj - yi) + xi:
                        inside = not inside
                    j = i
                if inside:
                    self.put(x, y, c)

    def line(self, x0, y0, x1, y1, c):
        n = max(abs(x1 - x0), abs(y1 - y0), 1)
        for i in range(n + 1):
            self.put(round(x0 + (x1 - x0) * i / n), round(y0 + (y1 - y0) * i / n), c)

    def mirror_left_to_right(self):
        """The face is symmetric by construction; this removes rounding differences between the two halves."""
        for y in range(H):
            for x in range(W // 2):
                self.p[y][W - 1 - x] = self.p[y][x]

    def outline(self):
        out = [r[:] for r in self.p]
        for y in range(H):
            for x in range(W):
                if self.p[y][x] == T and any(0 <= x + dx < W and 0 <= y + dy < H and self.p[y + dy][x + dx] not in (T, BLUE, INK, WHITE, GREY)
                                             for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1))):
                    out[y][x] = OUT
        self.p = out

    def rows(self):
        return ["".join("0123456789abcdef"[c] for c in r) for r in self.p]


def eye(c: Canvas, cx: int, kind: str):
    if kind in ("open", "wide", "up", "sad", "angry", "half"):
        rx, ry = (4, 5) if kind != "wide" else (5, 6)
        c.ellipse(cx, 36, rx, ry, WHITE)
        py = 34 if kind == "up" else 37
        c.ellipse(cx, py, 2.6 if kind != "wide" else 3.2, 2.6 if kind != "wide" else 3.2, INK)
        c.put(cx + 1, py - 1, WHITE)
        if kind == "half":
            for y in range(28, 37):
                for x in range(cx - 6, cx + 7):
                    c.put(x, y, FUR)
            for x in range(cx - 4, cx + 5):
                c.put(x, 36, INK)
        if kind == "sad":
            for i in range(-5, 6):
                c.put(cx + (i if cx < 32 else -i), 31 + (i + 5) // 5, FUR)
        if kind == "angry":
            for i in range(-5, 6):
                c.put(cx + (i if cx < 32 else -i), 33 - (i + 5) // 4 + 1, FUR)
    elif kind == "closed":
        for i in range(-3, 4):
            c.put(cx + i, 38 + (1 if abs(i) < 2 else 0), INK)
    elif kind == "happy":                                   # a smiling ^ shape, two pixels thick
        for i in range(-3, 4):
            y = 35 + (abs(i) + 1) // 2
            c.put(cx + i, y, INK)
            c.put(cx + i, y + 1, INK)


def mouth(c: Canvas, kind: str):
    if kind == "smile":
        for i, y in zip(range(-4, 5), (49, 50, 51, 51, 51, 51, 51, 50, 49)):
            c.put(32 + i, y, INK)
    elif kind in ("open", "grin"):
        c.ellipse(32, 50, 5, 3.5 if kind == "open" else 2.5, MOUTH)
        for x in range(26, 39):
            c.put(x, 47, INK)                               # upper lip line
    elif kind == "o":
        c.ellipse(32, 50, 2.5, 3, MOUTH)
    elif kind == "flat":
        for i in range(-4, 5):
            c.put(32 + i, 50, INK)
    elif kind == "frown":
        for i, y in zip(range(-4, 5), (52, 51, 50, 50, 50, 50, 50, 51, 52)):
            c.put(32 + i, y, INK)


def brows(c: Canvas, kind: str):
    off = {"n": (0, 0), "up": (-3, -3), "sad": (1, -2), "angry": (-2, 2)}[kind]
    for sx, (dl, dr) in ((-1, off), (1, off)):
        base = 23 if sx < 0 else 41
        for i in range(5):
            slope = (i - 2) * (dl if sx < 0 else -dl) // 4 if kind in ("sad", "angry") else 0
            c.put(base + i, 29 + (dl if kind in ("up",) else 0) + slope, INK)


def extra(c: Canvas, kind: str | None):
    if kind == "zzz":
        for (x, y, s) in ((50, 14, 4), (55, 8, 5)):
            for i in range(s):
                c.put(x + i, y, INK); c.put(x + s - 1 - i, y + i, INK); c.put(x + i, y + s - 1, INK)
    elif kind == "dots":
        for x, y, r in ((50, 18, 1), (54, 14, 1.5), (59, 9, 2)):
            c.ellipse(x, y, r, r, INK)
    elif kind == "waves":
        for dx in (6, 9):
            for y in range(32, 41):
                c.put(32 - 21 - dx, y, BLUE) if y in (33, 34, 38, 39) else None
                c.put(32 + 21 + dx, y, BLUE) if y in (33, 34, 38, 39) else None
    elif kind == "sparkle":
        for (x, y) in ((8, 12), (56, 14), (58, 34)):
            for d in range(-2, 3):
                c.put(x + d, y, LIGHT); c.put(x, y + d, LIGHT)
    elif kind == "heart":
        for (x, y) in ((52, 12), (56, 12), (51, 13), (52, 13), (53, 13), (55, 13), (56, 13), (57, 13), (52, 14), (53, 14), (54, 14), (55, 14), (56, 14), (53, 15), (54, 15), (55, 15), (54, 16)):
            c.put(x, y, BLUSH)
    elif kind == "tear":
        for y in range(40, 47):
            c.put(18, y, BLUE)
    elif kind == "question":
        for (x, y) in ((52, 8), (53, 7), (54, 7), (55, 8), (55, 9), (54, 10), (53, 11), (53, 13)):
            c.put(x, y, INK)


def draw(eyes: str, mouth_kind: str, brow: str, blush: int, ex: str | None, grey: bool = False) -> list[str]:
    c = Canvas()
    for sx in (-1, 1):                                     # ears
        bx = 32 + sx * 18
        c.poly([(bx - sx * 8, 28), (bx + sx * 2, 6), (bx + sx * 12, 24)], FUR)
        c.poly([(bx - sx * 4, 26), (bx + sx * 1, 13), (bx + sx * 8, 24)], CREAM)
    c.ellipse(32, 38, 22, 18, FUR)                         # head
    c.ellipse(32, 47, 12, 8, CREAM)                        # muzzle
    for x in range(31, 34):
        for y in range(40, 43):
            c.put(x, y, INK)                               # nose
    if blush:
        for sx in (-1, 1):
            c.ellipse(32 + sx * 17, 44, 3.5, 2, BLUSH)
    eye(c, 23, eyes); eye(c, 41, eyes)
    brows(c, brow)
    mouth(c, mouth_kind)
    c.mirror_left_to_right()
    extra(c, ex)
    c.outline()
    rows = c.rows()
    if grey:
        rows = [r.translate(str.maketrans("1234568", "aaaaaaa")) for r in rows]
    return rows


def make() -> dict:
    anims = {}
    for name in sorted(ANIMATIONS):
        eyes, m, b, bl, ex = SPEC[name]
        grey = ex == "grey"
        ex = None if grey else ex
        if m == "talk":
            frames = [{"ms": 160, "rows": draw(eyes, "smile", b, bl, ex)}, {"ms": 160, "rows": draw(eyes, "open", b, bl, ex)}]
        elif eyes in ("open", "wide") and name in ("idle", "listening", "curious", "surprised", "playing", "excited"):
            frames = [{"ms": 2400, "rows": draw(eyes, m, b, bl, ex, grey)}, {"ms": 140, "rows": draw("closed", m, b, bl, ex, grey)}]
        else:
            frames = [{"ms": 800, "rows": draw(eyes, m, b, bl, ex, grey)}]
        anims[name] = {"loop": True, "frames": frames}
    return {"version": 1, "name": "default", "width": W, "height": H,
            "screen": {"width": 240, "height": 240, "shape": "square"}, "palette": PALETTE, "animations": anims}
