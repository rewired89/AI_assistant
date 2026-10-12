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
    a = ap.parse_args(argv)
    try:
        views = {v: Path(a.dir) / f"{v}.png" for v in si.VIEWS}
        frames = {}
        for spec in a.anim:
            name, _, pattern = spec.partition("=")
            files = sorted(glob.glob(pattern))
            if not files:
                raise sp.SpriteError(f"no files match {pattern!r}")
            frames[name] = files
        doc = si.build_set(views, a.name, frames=frames, frame_ms=a.frame_ms)
        sp.save(a.out, doc)
    except (sp.SpriteError, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(f"wrote {a.out}: {doc['width']}x{doc['height']}, {len(doc['palette'])} colours, {len(doc['animations'])} animations")
    return 0


if __name__ == "__main__":
    sys.exit(main())
