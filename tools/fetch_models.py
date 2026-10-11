"""One-time download of the speech model, run by the owner on a machine with internet. After this the companion
never needs the network for speech.

    python -m tools.fetch_models whisper --size small.en --out models/whisper-small.en

Models come from the Hugging Face hub through faster-whisper's own converter repos; licenses are the model's
(Whisper weights are MIT as released by OpenAI; check the repo card of whatever you download)."""
from __future__ import annotations

import argparse
import sys


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    w = sub.add_parser("whisper")
    w.add_argument("--size", default="small.en")
    w.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    try:
        from faster_whisper.utils import download_model
        path = download_model(a.size, output_dir=a.out)
    except Exception as e:
        print(f"error: {type(e).__name__}: {e}", file=sys.stderr)
        return 1
    print(f"model saved to {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
