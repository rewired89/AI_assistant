# Screen art and the sprite editor

The robot's screen shows pixel sprites: one animation per protocol animation name (`idle`, `happy`, `listening`, `thinking`, `talking`, `sleeping`, ...), each a list of frames with a duration. Files live in `assets/sprites/*.json` (format and limits: `companion/sprites.py`, strict validation). The committed `assets/sprites/default.json` is built from the owner's own pixel art (48x48, five stills in `assets/sprites/source/`: front, right45, left45, left, right; made with an AI sprite generator, whose terms and the resemblance of the design to any franchise character MUST be checked before anything is sold; fine for a personal gift). The sprite-sheet export (`source/spritesheet.png` + `spritesheet.json`; the `.gif` is just a preview of the same frames) DOES hold 9 real animation frames (relaxed idle with a blink and mouth changes, front view). They are used as `idle` exactly as exported (200 ms each, like the gif) and give her real faces for talking (mouth open/closed), happy, laughing, surprised, sleeping (eyes shut) and more. The turn-of-the-head views and anything without a real frame still use the stills with generated motion: `companion/sprites_import.py` generates it: breathing/bobbing, hops, head turns between views, and small overlay symbols (z, hearts, ?, !, sparkles, tears). There are no facial expression changes (blinking, mouth) because the art has none: redraw frames in the editor, or export real animation frames from the art tool and import them. `companion/sprites_default.py` still draws an older built-in face set (menu: "Load the old built-in face art").

## Rebuild from the pictures
```powershell
pip install -e ".[art]"
python -m tools.import_sprites --dir assets/sprites/source --out assets/sprites/default.json          # stills only
python -m tools.import_sprites --dir assets/sprites/source --split-sheet assets/sprites/source/spritesheet.png assets/sprites/source/spritesheet.json --out -   # cut a sheet export into PNGs
python -m tools.import_sprites --dir assets/sprites/source --idle-frames "assets/sprites/source/sheet/idle_*.png" --out assets/sprites/default.json   # the committed set
python -m tools.import_sprites --dir assets/sprites/source --anim idle="exports/idle_*.png" --out assets/sprites/default.json   # real frames replace generated motion
```
Up to 64 colours (the art uses 61 after merging near-identical shades); each animation up to 12 frames.

## Edit it yourself
```powershell
python -m tools.desk_view            # only the editor, no robot needed
```
Open `http://localhost:8765/editor`. Pencil / eraser / fill / colour picker, left-right mirror (off by default; only for symmetric art), onion skin (previous frame faint), undo/redo, frames (copy, delete, reorder, per-frame milliseconds), animation loop flag, palette editor (16 colours max, colour 0 is see-through). **Save** writes `assets/sprites/default.json` (the previous version is kept as `default.bak`); the live view (`/`) reloads it within a second. Then commit it like any file: `git add assets; git commit; git push` (to `main`).
The **screen preview** shows the sprite on a screen of the size and shape you choose, with whole-pixel zoom, so you can see margins and the round-screen crop.

## What is NOT known yet (UNVERIFIED)
The real screen: resolution, shape, colour depth, refresh and how fast it can be updated. Defaults (240x240 square) are placeholders. When the board is identified, set the real values in the editor's preview boxes and decide the sprite size from them. Firmware does not read this format yet: a converter to the device's own format (e.g. C arrays in flash) comes after the display is known. Stage visuals (egg, 1 / 3 / 9 tails) exist only in the vector face for now, not in the sprite files.

## Safety of the editor
Local only (127.0.0.1), Host header checked (DNS rebinding), every save needs a custom header plus a matching Origin so another web page cannot overwrite the art, size limits (2 MB), strict validation, atomic write with backup. It can only write the one sprite file.
