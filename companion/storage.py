"""Checksummed, atomically replaced JSON file. Corrupt or missing -> default, flagged via .recovered."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path


class ChecksumJSON:
    def __init__(self, path: Path | str):
        self.path, self.recovered = Path(path), False

    @staticmethod
    def _digest(body: dict) -> str:
        return hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()

    def save(self, body: dict) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"body": body, "sha256": self._digest(body)}))
        os.replace(tmp, self.path)

    def load(self, default: dict) -> dict:
        self.recovered = False
        if not self.path.exists():
            return default
        try:
            blob = json.loads(self.path.read_text())
            if blob["sha256"] != self._digest(blob["body"]) or not isinstance(blob["body"], dict):
                raise ValueError("checksum")
            return blob["body"]
        except (ValueError, KeyError, TypeError, OSError):
            self.recovered = True
            return default
