"""One-time download of the speech model, run by the owner on a machine with internet. After this the companion
never needs the network for speech.

    python -m tools.fetch_models whisper --size base.en --out models/whisper-base.en
    python -m tools.fetch_models kokoro --out models/kokoro        # natural female voice, checksum-verified

Models come from the Hugging Face hub through faster-whisper's own converter repos; licenses are the model's
(Whisper weights are MIT as released by OpenAI; check the repo card of whatever you download)."""
from __future__ import annotations

import argparse
import hashlib
import sys
import urllib.request
from pathlib import Path

KOKORO_BASE = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/"
KOKORO_FILES = {   # sha256 pinned from the files checked on 2026-10-12; a mismatch means the download is not what we tested
    "kokoro-v1.0.onnx": "7d5df8ecf7d4b1878015a32686053fd0eebe2bc377234608764cc0ef3636a6c5",
    "voices-v1.0.bin": "bca610b8308e8d99f32e6fe4197e7ec01679264efed0cac9140fe9c29f1fbf7d",
}


def _kokoro(out: Path) -> int:
    out.mkdir(parents=True, exist_ok=True)
    for name, want in KOKORO_FILES.items():
        dest = out / name
        if dest.exists() and hashlib.sha256(dest.read_bytes()).hexdigest() == want:
            print(f"{name}: already downloaded")
            continue
        print(f"downloading {name} ...")
        try:
            with urllib.request.urlopen(KOKORO_BASE + name, timeout=120) as r:
                data = r.read()
        except Exception as e:
            print(f"error: {type(e).__name__}: {e}", file=sys.stderr)
            return 1
        if hashlib.sha256(data).hexdigest() != want:
            print(f"error: {name} does not match the expected checksum; not saved", file=sys.stderr)
            return 1
        dest.write_bytes(data)
    print(f"voice saved to {out}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    w = sub.add_parser("whisper")
    w.add_argument("--size", default="small.en")
    w.add_argument("--out", required=True)
    k = sub.add_parser("kokoro", help="natural offline voice (about 350 MB), from GitHub releases")
    k.add_argument("--out", default="models/kokoro")
    a = ap.parse_args(argv)
    if a.cmd == "kokoro":
        return _kokoro(Path(a.out))
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
