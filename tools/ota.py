"""Sign and inspect firmware packages. Run this on the owner's machine, never on the robot or the companion host.

    python -m tools.ota keygen secrets/ota-signing.key --pub secrets/ota-signing.pub
    python -m tools.ota build firmware.bin --key secrets/ota-signing.key --version 0.2.0 --build 2 --board <board-id> --out dist
    python -m tools.ota verify dist/nine-tails-0.2.0-b2.manifest.json --pub secrets/ota-signing.pub
    python -m tools.ota info dist/nine-tails-0.2.0-b2.manifest.json

The private key is written 0600 and is never overwritten. Keep a backup offline: losing it means no more updates
without re-flashing by cable. The public key (DER) is what the device trusts."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from companion.security.ota_signing import (OtaFormatError, Package, build_package, check_package, generate_signing_key,
                                            load_signing_key)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    k = sub.add_parser("keygen")
    k.add_argument("key")
    k.add_argument("--pub", required=True)
    b = sub.add_parser("build")
    b.add_argument("image")
    b.add_argument("--key", required=True)
    b.add_argument("--version", required=True)
    b.add_argument("--build", type=int, required=True)
    b.add_argument("--board", required=True)
    b.add_argument("--product", default="nine-tails")
    b.add_argument("--kid", type=int, default=1)
    b.add_argument("--requires-build", type=int, default=0)
    b.add_argument("--out", default="dist")
    v = sub.add_parser("verify")
    v.add_argument("manifest")
    v.add_argument("--pub", required=True)
    v.add_argument("--kid", type=int, default=1)
    i = sub.add_parser("info")
    i.add_argument("manifest")
    a = ap.parse_args(argv)
    try:
        if a.cmd == "keygen":
            Path(a.pub).write_bytes(generate_signing_key(a.key))
            print(f"private key {a.key} (keep offline), public key {a.pub}")
        elif a.cmd == "build":
            pkg = build_package(Path(a.image).read_bytes(), load_signing_key(a.key), product=a.product, board=a.board,
                                version=a.version, build=a.build, sign_kid=a.kid, requires_build=a.requires_build)
            img, man = pkg.save(a.out)
            print(f"wrote {img} and {man}")
        elif a.cmd == "verify":
            check_package(Package.load(a.manifest), {a.kid: Path(a.pub).read_bytes()})
            print("ok: image matches manifest and signature is valid")
        else:
            pkg = Package.load(a.manifest)
            for key, val in sorted(pkg.manifest.items()):
                print(f"{key}: {val}")
    except (OtaFormatError, FileExistsError, OSError, KeyError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
