# Sprite workbench (see her on your computer before the robot exists)

## Start it
```powershell
git pull origin main
./.venv/bin/Activate.ps1
python -m tools.workbench
```
It prints `Workbench: http://localhost:8765/workbench` and opens your browser. If it does not open, copy that address into the browser. Ctrl+C in the terminal stops it. If port 8765 is busy: `python -m tools.workbench --port 8780`.
Everything it shows is SIMULATED: no robot, no AI, no internet, no keys.

## What you see
- **Left**: every sprite set (adult, baby, egg) and every animation, grouped idle / emotions / movement / interaction / sleep / system. Each row shows size, frame count, speed and a status badge. Click one to play it.
- **Middle**: the robot screen. Choose the screen size (the 240x240 one is the vendor's claim and UNVERIFIED), shape, sprite size, window zoom, and a dark, light or checker background. Play/Pause (or Space), Restart, frame step (arrow keys or click a thumbnail), Speed, loop. State buttons trigger every state. Missing art shows a red striped screen that says MISSING ART.
- **Right**: a zoomed inspector (hover shows the pixel's position and palette colour), and the interaction simulator: pick a personality (ninetales, cubone) and mood, then press events (message, speak, listen, think, wake, sleep, low battery, touch...). The real behaviour rules decide which animation and which scripted line come out. Text marked `[sample ...]` is made up, not AI.

## Status badges (honest)
- **real frames**: drawn frames from the art tool (only the adult's nine idle frames and the expressions built from them).
- **composed**: built from her real still poses with a small shift or an original overlay symbol (heart, sparkle, battery...). Never stretched.
- **placeholder**: a stand-in, NOT final art. Today: blinking (a real closed-eye frame, but the head pose jumps a little) and walking (the side view bobbing in place).
- **missing**: no frames exist: tail wagging, stretching, lying down (and blinking for the baby).
- **not applicable**: the egg has no movement clips.

## Change a picture and see it
1. Her pictures live in `assets/sprites/source/` (adult), `source_baby/`, `source_egg/`: `front.png`, `right45.png`, `left45.png`, `left.png`, `right.png` (same size, transparent background, 8 to 128 px, up to 56 colours) and, for the adult, nine idle frames `source/sheet/idle_0.png` ... `idle_8.png`.
2. Replace a PNG, keeping the same name. The page notices within 2 seconds, rebuilds that set and shows it ("rebuilt ..." message). A broken picture shows an error message and leaves the old set in place.
3. A sprite-sheet export (PNG plus JSON, 8 directions): `python -m tools.workbench import-sheet baby --png path/sheet.png --json path/sheet.json`.
4. To edit single pixels: `python -m tools.desk_view` then open `http://localhost:8765/editor`. Careful: replacing a source PNG rebuilds the set and overwrites pixel edits made in the editor for that set.
5. Check everything: `python -m tools.workbench check` (missing sets or frames, bad frame references, a manifest that claims art that is not there).

## Files
- `assets/sprites/<set>.json`: the sprite set (palette, animations, frames). The ONE source of truth the simulator and the firmware both use.
- `assets/sprites/manifest.json`: describes every animation (category, label, status, notes) and the screen presets. Add a new clip here and in `companion/sprites.py` (`EXTRA_ANIMATIONS`).
- `companion/workbench.py`: catalog, checks, simulator, rebuild. `tools/workbench.py`: launcher. `tools/workbench_page.py`: the page.
- `tools/export_sprites.py`: turns a set into the compact `.ntsp` file for the robot (`python -m tools.export_sprites default --verify`). It stores palette indices; the screen's real pixel format is UNVERIFIED, so the device adapter must convert once the board is known.

## Limits (read this)
- The simulated screen is an approximation. The real display size, shape, colour depth and refresh are UNVERIFIED.
- Only the 21 protocol animations can be requested from the robot. The extra clips (blink, look left/right, walk, low battery, and the missing three) are workbench-only until the protocol carries them.
- No firmware renders these files yet; the export format is a proposal checked only by reading it back in Python.
- Missing art: tail wagging, stretching, lying down, a true blink, a real walk cycle. These need new pixel art.
