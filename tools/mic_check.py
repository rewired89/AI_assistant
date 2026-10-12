"""Is the microphone reaching the computer? A 5-second check that shows a live level bar and says what is wrong.

    python -m tools.mic_check                       # default input
    python -m tools.mic_check --device plughw:1,0   # a specific one (list them with: arecord -l)

Speak while it runs. It uses exactly the same recording path as the desk robot, so if this works and the desk robot does
not, the problem is elsewhere; if this shows nothing, the problem is the microphone, its volume/mute, or the device name."""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import time

from companion.speech.vad import rms
from tools.desk_robot import ArecordMic

BAR = 40


def check(mic, seconds: float = 5.0, out=print, per_line_s: float = 0.2) -> dict:
    """Returns {'verdict': ..., 'peak': ..., 'status': ...}. `mic` has read()/status()/close()."""
    t0, peak, levels, last_print = time.time(), 0.0, [], 0.0
    while time.time() - t0 < seconds:
        chunk = mic.read()
        if chunk:
            lvl = rms(chunk)
            levels.append(lvl)
            peak = max(peak, lvl)
            if time.time() - last_print >= per_line_s:
                last_print = time.time()
                out(f"{lvl:7.0f} |" + "#" * min(BAR, int(lvl / 400)))
        else:
            time.sleep(0.02)
    status = mic.status()
    if not levels:
        verdict = "NO AUDIO: " + status
    elif peak < 30:
        verdict = "SILENT: audio arrives but it is all zeros. The microphone is muted or its input volume is 0 (check alsamixer / system sound settings, or the wrong device)."
    elif peak < 300:
        verdict = "VERY QUIET: raise the input volume, or speak closer."
    elif sorted(levels)[len(levels) // 2] > 12000:      # judged on the typical level, not one startup click
        verdict = "TOO LOUD: the level is very high (normal speech is roughly 1000 to 6000). Lower the input volume; loud, clipped audio makes speech detection and recognition worse."
    else:
        verdict = "OK: the microphone works."
    med = sorted(levels)[len(levels) // 2] if levels else 0.0
    out(f"levels: median {med:.0f}, peak {peak:.0f} (quiet room should be well under 500 median)")
    return {"verdict": verdict, "peak": peak, "median": med, "status": status}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--device")
    ap.add_argument("--seconds", type=float, default=5.0)
    a = ap.parse_args(argv)
    if shutil.which("arecord") is None:
        print("arecord is not installed: sudo apt install alsa-utils", file=sys.stderr)
        return 1
    print("Input devices (arecord -l):")
    print(subprocess.run(["arecord", "-l"], capture_output=True, text=True).stdout or "  (none listed)")
    print(f"Recording {a.seconds:.0f} s. SPEAK NOW.")
    mic = ArecordMic(a.device)
    try:
        r = check(mic, a.seconds)
    finally:
        mic.close()
    print(r["verdict"])
    return 0 if r["verdict"].startswith("OK") else 1


if __name__ == "__main__":
    sys.exit(main())
