"""Packs the three sprite sets into one file for the robot's `sprites` flash partition.

    python -m tools.sprite_bundle                 # writes build/sprites.bin and prints the flash command
    python -m tools.sprite_bundle --check build/sprites.bin

Format NTSB v1 (little endian): 'NTSB', version u8, count u8, then per set: name[8] (NUL padded), offset u32, size u32
(offsets from the start of the file), followed by the NTSP v1 blobs from tools.export_sprites (4-byte aligned).
Sets: egg -> assets/sprites/egg.json, baby -> baby.json, adult -> default.json. Partition: 0x310000, 1 MiB (firmware/voice_test/partitions.csv).
"""
from __future__ import annotations

import argparse
import json
import struct
import sys
from pathlib import Path

from tools.export_sprites import export, load

ROOT = Path(__file__).resolve().parent.parent
SETS = [("egg", "egg.json"), ("baby", "baby.json"), ("adult", "default.json")]
PARTITION_OFFSET, PARTITION_SIZE = 0x310000, 0x100000


def build(sprites_dir: Path) -> bytes:
    blobs = [(name, export(json.loads((sprites_dir / f).read_text()))) for name, f in SETS]
    head = 6 + 16 * len(blobs)
    out = bytearray(b"NTSB" + struct.pack("<BB", 1, len(blobs)))
    pos, table, body = (head + 3) & ~3, bytearray(), bytearray()
    for name, blob in blobs:
        table += name.encode().ljust(8, b"\0") + struct.pack("<II", pos + len(body), len(blob))
        body += blob + b"\0" * (-len(blob) % 4)
    out += table + b"\0" * (pos - head) + body
    if len(out) > PARTITION_SIZE:
        raise SystemExit(f"bundle is {len(out)} bytes, partition holds {PARTITION_SIZE}")
    return bytes(out)


def check(data: bytes) -> list[str]:
    if data[:4] != b"NTSB" or data[4] != 1:
        raise SystemExit("not an NTSB v1 file")
    n, sets = data[5], []
    for i in range(n):
        name, off, size = struct.unpack_from("<8sII", data, 6 + 16 * i)
        doc = load(data[off:off + size])
        sets.append(f"{name.rstrip(bytes(1)).decode()}: {size} bytes, {len(doc['animations'])} animations")
    return sets


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="build/sprites.bin")
    ap.add_argument("--check")
    a = ap.parse_args()
    if a.check:
        print("\n".join(check(Path(a.check).read_bytes())))
        return 0
    data = build(ROOT / "assets" / "sprites")
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(data)
    print("\n".join(check(data)))
    print(f"wrote {out} ({len(data)} bytes). Flash it from the ESP-IDF bash terminal:")
    print(f"  python -m esptool --chip esp32s3 -p /dev/ttyACM0 write_flash 0x{PARTITION_OFFSET:x} {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
