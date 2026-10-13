"""Copy the device id and key from a provisioning bundle into a firmware project's local sdkconfig (git-ignored), so nothing is typed or pasted.

    python -m tools.fw_config --bundle deploy/ninetails-01.bundle.json --project firmware/net_test

The key is a secret: sdkconfig is ignored by git. Never paste it into chat or commit it. After flashing, delete the bundle file."""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


def apply(bundle: Path, project: Path) -> None:
    b = json.loads(bundle.read_text())
    if not re.fullmatch(r"[0-9a-f]{64}", b.get("key", "")) or not re.fullmatch(r"[A-Za-z0-9_-]{3,32}", b.get("device_id", "")):
        raise ValueError("bundle has no valid device_id and 64-hex key")
    cfg = project / "sdkconfig"
    if not cfg.exists():
        raise FileNotFoundError(f"{cfg} not found: run `idf.py set-target esp32s3` once in that folder first")
    lines = cfg.read_text().splitlines()
    want = {"CONFIG_NT_DEVICE_ID": f'"{b["device_id"]}"', "CONFIG_NT_DEVICE_KEY_HEX": f'"{b["key"]}"'}
    seen = set()
    for i, ln in enumerate(lines):
        m = re.match(r"#?\s*(CONFIG_NT_DEVICE_(?:ID|KEY_HEX))[ =]", ln)
        if m and m.group(1) in want:
            lines[i] = f"{m.group(1)}={want[m.group(1)]}"
            seen.add(m.group(1))
    lines += [f"{k}={v}" for k, v in want.items() if k not in seen]
    cfg.write_text("\n".join(lines) + "\n")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--bundle", required=True)
    ap.add_argument("--project", required=True)
    a = ap.parse_args(argv)
    try:
        apply(Path(a.bundle), Path(a.project))
    except (ValueError, FileNotFoundError, OSError, json.JSONDecodeError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(f"device id and key written to {a.project}/sdkconfig (git-ignored). Build and flash next.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
