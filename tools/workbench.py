"""Nine Tails sprite workbench: see and test her sprites and animations on your computer (no robot needed).

    python -m tools.workbench                  # starts it and prints the address (open http://localhost:8765/workbench)
    python -m tools.workbench check            # checks the sprite files and manifest for problems
    python -m tools.workbench rebuild default  # rebuild assets/sprites/default.json from the source PNGs (also done automatically)
    python -m tools.workbench import-sheet baby --png sheet.png --json sheet.json   # use a Pixel Lab style sheet export

Everything it shows is SIMULATED: no device, no AI, no internet."""
from __future__ import annotations

import argparse
import shutil
import sys
import time
import webbrowser
from pathlib import Path

from companion import sprites as sp
from companion import sprites_import as si
from companion import workbench as wb
from tools.desk_view import ViewServer


def import_sheet(name: str, png: str, meta: str, sprite_dir: Path = wb.SPRITES) -> str:
    """Cuts a sprite-sheet export into the set's source pictures (south->front, south-east->right45, east->right,
    south-west->left45, west->left) and rebuilds the set."""
    m = wb.load_manifest(sprite_dir / "manifest.json")
    info = m["sets"].get(name)
    if not info or not info.get("source_dir"):
        raise sp.SpriteError(f"unknown set {name!r}; known: {sorted(m['sets'])}")
    src = sprite_dir / info["source_dir"]
    parts = si.split_sheet(png, meta, src / "sheet")
    for want, have in (("front", "south"), ("right45", "south-east"), ("right", "east"), ("left45", "south-west"), ("left", "west")):
        shutil.copyfile(src / "sheet" / f"{have}.png", src / f"{want}.png")
    shutil.copyfile(png, src / "spritesheet.png")
    shutil.copyfile(meta, src / "spritesheet.json")
    return wb.rebuild_set(name, sprite_dir, m)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd")
    run = sub.add_parser("run")
    for p in (ap, run):
        p.add_argument("--port", type=int, default=8765)
        p.add_argument("--no-browser", action="store_true")
    sub.add_parser("check")
    r = sub.add_parser("rebuild"); r.add_argument("name")
    i = sub.add_parser("import-sheet"); i.add_argument("name"); i.add_argument("--png", required=True); i.add_argument("--json", required=True)
    a = ap.parse_args(argv)
    try:
        if a.cmd == "check":
            problems = wb.check()
            for p in problems:
                print("PROBLEM:", p)
            c = wb.catalog()
            for s in c["sets"]:
                counts = {}
                for x in s["animations"]:
                    counts[x["status"]] = counts.get(x["status"], 0) + 1
                print(f"{s['name']}: {counts}")
            print("clean" if not problems else f"{len(problems)} problem(s)")
            return 1 if problems else 0
        if a.cmd == "rebuild":
            print(wb.rebuild_set(a.name))
            return 0
        if a.cmd == "import-sheet":
            print(import_sheet(a.name, a.png, a.json))
            return 0
        state = {"anim": "idle", "power": "AWAKE", "battery": 100, "mic": True, "pipe": "idle", "stage": "NINE_TAILS", "caption": "",
                 "speaking": False, "notes": [], "level": 0, "threshold": 0, "heard": ""}
        v = ViewServer(lambda: state, lambda c, x: None, port=a.port)
        url = v.start()
        print(f"Workbench: {url}/workbench   (Ctrl+C to stop)\nEverything shown is simulated.")
        if not a.no_browser:
            try:
                webbrowser.open(f"{url}/workbench")
            except Exception:
                pass
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            v.stop()
        return 0
    except (sp.SpriteError, wb.ManifestError, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
