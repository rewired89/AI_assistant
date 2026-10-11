"""Encrypt an existing plaintext memory file in place (with a verified copy first).

    python -m tools.memory_crypt encrypt data\\memory.db --user her

The passphrase is read from the NINETAILS_MEMORY_PASSPHRASE environment variable or prompted for. Keep it OUT of
the folder that holds the memory files: next to the file it protects nothing. If you lose it, the memories are gone.
The old plaintext file is deleted but its disk blocks are not scrubbed; treat older backups as plaintext."""
from __future__ import annotations

import argparse
import getpass
import os
import sys

from companion.memory import CryptoError, MemoryError_, encrypt_existing


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("encrypt")
    e.add_argument("path")
    e.add_argument("--user", required=True)
    a = ap.parse_args(argv)
    pw = os.environ.get("NINETAILS_MEMORY_PASSPHRASE")
    if not pw:
        pw = getpass.getpass("New passphrase: ")
        if getpass.getpass("Repeat: ") != pw:
            print("error: passphrases differ", file=sys.stderr)
            return 1
    try:
        n = encrypt_existing(a.path, a.user, pw)
    except (MemoryError_, CryptoError, OSError) as ex:
        print(f"error: {ex}", file=sys.stderr)
        return 1
    print(f"encrypted {n} memories in {a.path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
