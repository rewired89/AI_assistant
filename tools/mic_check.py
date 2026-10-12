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


def stats(pcm: bytes) -> dict:
    """Plain numbers about a recording: typical level, peak, constant offset, share of clipped samples."""
    import struct
    n = len(pcm) // 2
    if n == 0:
        return {"samples": 0}
    x = struct.unpack(f"<{n}h", pcm[:n * 2])
    mean = sum(x) / n
    step = 1600
    lv = sorted(rms(pcm[i * 2:(i + step) * 2]) for i in range(0, n - step + 1, step)) or [0.0]
    return {"samples": n, "median_level": round(lv[len(lv) // 2]), "peak_sample": max(abs(v) for v in x),
            "dc_offset": round(mean), "clipped_pct": round(100 * sum(1 for v in x if abs(v) >= 32760) / n, 2)}


def compare(device: str | None, seconds: float = 4.0, out=print) -> None:
    """Records the SAME seconds two ways, the way that worked in bench_stt (arecord to a WAV file) and the way the desk
    robot does it (raw bytes through a pipe), and prints numbers for both. Saves both so you can play them."""
    import wave
    dev = ["-D", device] if device else []
    out(f"1/2 file method (arecord -> mic_file.wav, {seconds:.0f} s). SPEAK NOW.")
    r = subprocess.run(["arecord", "-q", "-f", "S16_LE", "-r", "16000", "-c", "1", "-d", str(int(seconds)), *dev, "mic_file.wav"],
                       capture_output=True, text=True)
    if r.returncode:
        out("   arecord failed: " + (r.stderr.strip() or str(r.returncode)))
    else:
        with wave.open("mic_file.wav") as w:
            out(f"   {w.getframerate()} Hz, {w.getnchannels()} ch, {w.getsampwidth() * 8} bit -> {stats(w.readframes(w.getnframes()))}")
    out(f"2/2 pipe method (what the desk robot uses -> mic_pipe.wav, {seconds:.0f} s). SPEAK NOW.")
    mic, buf, t0 = ArecordMic(device), b"", time.time()
    try:
        while time.time() - t0 < seconds:
            c = mic.read()
            buf += c or b""
            if not c:
                time.sleep(0.02)
        out("   status: " + mic.status())
    finally:
        mic.close()
    with wave.open("mic_pipe.wav", "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000); w.writeframes(buf)
    out(f"   {stats(buf)}")
    out("Play them: aplay mic_file.wav ; aplay mic_pipe.wav")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--device")
    ap.add_argument("--seconds", type=float, default=5.0)
    ap.add_argument("--compare", action="store_true", help="record both ways (file vs pipe) and print numbers")
    a = ap.parse_args(argv)
    if shutil.which("arecord") is None:
        print("arecord is not installed: sudo apt install alsa-utils", file=sys.stderr)
        return 1
    if a.compare:
        compare(a.device, a.seconds)
        return 0
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
