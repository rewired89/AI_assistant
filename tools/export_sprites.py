"""Turns a sprite set (assets/sprites/<name>.json, the one source of truth) into the compact binary the robot's firmware
will read, and reads it back to prove nothing was lost.

    python -m tools.export_sprites default --out build/default.ntsp
    python -m tools.export_sprites default --verify          # export, read back, compare

Format NTSP v1 (little endian): 'NTSP', version u8, width u8, height u8, colours u8, animations u8;
colours x (r,g,b,a); then per animation: name length u8, name, loop u8, frames u8, then per frame: ms u16 and width*height
palette-index bytes. One byte per pixel on purpose: the real display's pixel format is UNVERIFIED, so converting indices to
RGB565 (or anything else) is the job of the device adapter once the screen is known, not of this file."""
from __future__ import annotations

import argparse
import struct
import sys
from pathlib import Path

from companion import sprites as sp

MAGIC, VERSION = b"NTSP", 1


def export(doc: dict) -> bytes:
    sp.validate(doc)
    w, h = doc["width"], doc["height"]
    out = bytearray(MAGIC + struct.pack("<BBBBB", VERSION, w, h, len(doc["palette"]), len(doc["animations"])))
    for c in doc["palette"]:
        out += bytes.fromhex(c[1:7]) + bytes.fromhex(c[7:9] if len(c) > 7 else "ff")
    for name, a in sorted(doc["animations"].items()):
        nb = name.encode()
        out += struct.pack("<B", len(nb)) + nb + struct.pack("<BB", 1 if a.get("loop", True) else 0, len(a["frames"]))
        for f in a["frames"]:
            out += struct.pack("<H", f["ms"]) + bytes(sp.ALPHABET.index(ch) for row in f["rows"] for ch in row)
    return bytes(out)


def load(data: bytes) -> dict:
    """Reads NTSP back into the JSON shape (used by the tests and to check a file before flashing)."""
    if data[:4] != MAGIC or data[4] != VERSION:
        raise sp.SpriteError("not an NTSP v1 file")
    _, w, h, nc, na = struct.unpack_from("<BBBBB", data, 4)
    p = 9
    palette = []
    for _ in range(nc):
        r, g, b, a = data[p:p + 4]
        palette.append(f"#{r:02x}{g:02x}{b:02x}" + ("" if a == 255 else f"{a:02x}"))
        p += 4
    anims = {}
    for _ in range(na):
        n = data[p]
        name = data[p + 1:p + 1 + n].decode()
        p += 1 + n
        loop, nf = data[p], data[p + 1]
        p += 2
        frames = []
        for _ in range(nf):
            (ms,) = struct.unpack_from("<H", data, p)
            p += 2
            px = data[p:p + w * h]
            p += w * h
            frames.append({"ms": ms, "rows": ["".join(sp.ALPHABET[v] for v in px[y * w:(y + 1) * w]) for y in range(h)]})
        anims[name] = {"loop": bool(loop), "frames": frames}
    if p != len(data):
        raise sp.SpriteError("trailing bytes")
    return {"version": 1, "name": "export", "width": w, "height": h, "palette": palette, "animations": anims}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("name")
    ap.add_argument("--out")
    ap.add_argument("--verify", action="store_true")
    ap.add_argument("--dir", default=str(Path(__file__).resolve().parent.parent / "assets" / "sprites"))
    a = ap.parse_args(argv)
    try:
        doc = sp.load(Path(a.dir) / f"{a.name}.json")
        data = export(doc)
        if a.verify:
            back = load(data)
            same = back["animations"] == {k: {"loop": v.get("loop", True), "frames": v["frames"]} for k, v in doc["animations"].items()}
            if not same or back["palette"] != [c if len(c) > 7 else c for c in doc["palette"]]:
                print("MISMATCH between the JSON and its export", file=sys.stderr)
                return 1
            print(f"{a.name}: {len(data)} bytes, read back identical ({len(doc['animations'])} animations)")
        if a.out:
            Path(a.out).parent.mkdir(parents=True, exist_ok=True)
            Path(a.out).write_bytes(data)
            print(f"wrote {a.out}")
    except (sp.SpriteError, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
