"""Turn pictures of the character into the robot's sprite set (see companion/sprites_import.py).

    python -m tools.import_sprites --dir assets/sprites/source --out assets/sprites/default.json

--dir holds front.png, right45.png, left45.png, left.png, right.png (same size, transparent background).
Real animation frames exported from your art tool replace the generated motion for one animation:
    python -m tools.import_sprites --dir assets/sprites/source --anim idle=exports/idle_*.png --out assets/sprites/default.json
Then open the editor to fix anything: python -m tools.desk_view"""
from __future__ import annotations

import argparse
import glob
import sys
from pathlib import Path

from companion import sprites as sp
from companion import sprites_import as si


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--name", default="default")
    ap.add_argument("--anim", action="append", default=[], help="NAME=glob of numbered PNG frames, e.g. idle=exports/idle_*.png")
    ap.add_argument("--frame-ms", type=int, default=120)
    ap.add_argument("--idle-frames", help="glob of the 9 real idle frames (see split-sheet); gives real expressions")
    ap.add_argument("--idle-ms", type=int, default=200)
    ap.add_argument("--no-extras", action="store_true", help="skip the workbench clips (look, walk, low battery, blink); for the egg")
    ap.add_argument("--split-sheet", nargs=2, metavar=("SHEET_PNG", "EXPORT_JSON"),
                    help="first cut a sprite-sheet export into PNGs inside --dir (rotations and the animation row)")
    a = ap.parse_args(argv)
    try:
        if a.split_sheet:
            parts = si.split_sheet(a.split_sheet[0], a.split_sheet[1], Path(a.dir) / "sheet")
            print("cut", {k: len(v) for k, v in parts.items()}, "into", Path(a.dir) / "sheet")
            if not a.out or a.out == "-":
                return 0
        views = {v: Path(a.dir) / f"{v}.png" for v in si.VIEWS}
        frames = {}
        for spec in a.anim:
            name, _, pattern = spec.partition("=")
            files = sorted(glob.glob(pattern))
            if not files:
                raise sp.SpriteError(f"no files match {pattern!r}")
            frames[name] = files
        idle = sorted(glob.glob(a.idle_frames)) if a.idle_frames else None
        if a.idle_frames and not idle:
            raise sp.SpriteError(f"no files match {a.idle_frames!r}")
        doc = si.build_set(views, a.name, frames=frames, frame_ms=a.frame_ms, idle_frames=idle, idle_ms=a.idle_ms,
                          extras=not a.no_extras)
        sp.save(a.out, doc)
    except (sp.SpriteError, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(f"wrote {a.out}: {doc['width']}x{doc['height']}, {len(doc['palette'])} colours, {len(doc['animations'])} animations")
    return 0


if __name__ == "__main__":
    sys.exit(main())
