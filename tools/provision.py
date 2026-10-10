"""Manage per-device keys on the host.

    python -m tools.provision create ninetails-01 --url wss://ninetails.local:8443 --ca secrets/tls/ca.pem
    python -m tools.provision list
    python -m tools.provision revoke ninetails-01

`create` writes a provisioning bundle (the device key + pinned CA + host URL). Deliver it to the robot over
USB at flash time, then DELETE the file. Passphrase for the key file comes from NINETAILS_KEY_PASSPHRASE.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

from companion.security import SecretStore, SecretStoreError


def _store(path: str) -> SecretStore:
    return SecretStore(path, time.time, os.environ.get("NINETAILS_KEY_PASSPHRASE") or None)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--store", default="secrets/keys.json")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("create")
    c.add_argument("device_id")
    c.add_argument("--url", required=True)
    c.add_argument("--ca", required=True, help="path to the public CA certificate (ca.pem)")
    c.add_argument("--out", default=None, help="bundle path (default secrets/<device_id>.bundle.json)")
    sub.add_parser("list")
    r = sub.add_parser("revoke")
    r.add_argument("device_id")
    a = ap.parse_args(argv)
    try:
        store = _store(a.store)
        if a.cmd == "create":
            bundle = store.create_device(a.device_id, Path(a.ca).read_text(), a.url)
            out = Path(a.out or f"secrets/{a.device_id}.bundle.json")
            out.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w") as f:
                json.dump(bundle, f, indent=2)
            print(f"bundle written: {out}\nFlash it over USB, then delete it. It contains the device key.")
        elif a.cmd == "list":
            for d in store.devices():
                print(json.dumps(d))
        else:
            store.revoke(a.device_id)
            print(f"revoked {a.device_id}")
    except (SecretStoreError, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
