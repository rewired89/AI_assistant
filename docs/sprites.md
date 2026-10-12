# Screen art and the sprite editor

The robot's screen shows pixel sprites: one animation per protocol animation name (`idle`, `happy`, `listening`, `thinking`, `talking`, `sleeping`, ...), each a list of frames with a duration. Files live in `assets/sprites/*.json` (format and limits: `companion/sprites.py`, strict validation). The default set is original starter art drawn by `companion/sprites_default.py`; replace it by editing.

## Edit it yourself
```powershell
python -m tools.desk_view            # only the editor, no robot needed
```
Open `http://localhost:8765/editor`. Pencil / eraser / fill / colour picker, left-right mirror (on by default, keeps the face symmetric), onion skin (previous frame faint), undo/redo, frames (copy, delete, reorder, per-frame milliseconds), animation loop flag, palette editor (16 colours max, colour 0 is see-through). **Save** writes `assets/sprites/default.json` (the previous version is kept as `default.bak`); the live view (`/`) reloads it within a second. Then commit it like any file: `git add assets; git commit; git push` (to `main`).
The **screen preview** shows the sprite on a screen of the size and shape you choose, with whole-pixel zoom, so you can see margins and the round-screen crop.

## What is NOT known yet (UNVERIFIED)
The real screen: resolution, shape, colour depth, refresh and how fast it can be updated. Defaults (240x240 square) are placeholders. When the board is identified, set the real values in the editor's preview boxes and decide the sprite size from them. Firmware does not read this format yet: a converter to the device's own format (e.g. C arrays in flash) comes after the display is known. Stage visuals (egg, 1 / 3 / 9 tails) exist only in the vector face for now, not in the sprite files.

## Safety of the editor
Local only (127.0.0.1), Host header checked (DNS rebinding), every save needs a custom header plus a matching Origin so another web page cannot overwrite the art, size limits (2 MB), strict validation, atomic write with backup. It can only write the one sprite file.
