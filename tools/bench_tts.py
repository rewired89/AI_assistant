"""Measure a text-to-speech voice on THIS machine and save a WAV you can listen to.

    python -m tools.bench_tts --engine kokoro --model models/kokoro --voice af_heart --out /tmp/voice.wav
    python -m tools.bench_tts --engine espeak

RTF (real-time factor) = time to make the audio / length of the audio. Under 1.0 she can talk without gaps."""
from __future__ import annotations

import argparse
import struct
import sys
import time
import wave

from companion.speech.engines import EngineError, EspeakSynthesizer, KokoroSynthesizer

LINES = ["Hello, my name is Kit. It's so nice to meet you.",
         "How should I call you?",
         "I looked at the clouds today, and one of them looked just like a fox. Isn't that funny?"]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--engine", choices=["kokoro", "espeak"], default="kokoro")
    ap.add_argument("--model")
    ap.add_argument("--voice", default="af_heart")
    ap.add_argument("--out")
    a = ap.parse_args(argv)
    try:
        t0 = time.time()
        tts = KokoroSynthesizer(a.model, voice=a.voice) if a.engine == "kokoro" else EspeakSynthesizer()
        print(f"load: {time.time() - t0:.1f} s")
        pcm = b""
        for i, line in enumerate(LINES * 2):
            t = time.time()
            chunk = tts.synthesize(line, "happy" if i % 2 else "normal")
            dt, secs = time.time() - t, len(chunk) / 32000
            print(f"{'warm' if i >= len(LINES) else 'cold'}: {secs:.1f} s of audio in {dt:.1f} s  (RTF {dt / secs:.2f})  {line[:40]!r}")
            if i < len(LINES):
                pcm += chunk + bytes(8000)
    except (EngineError, TypeError, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    if a.out:
        with wave.open(a.out, "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
            w.writeframes(pcm)
        print(f"saved {a.out} (play it with: aplay {a.out})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
