from __future__ import annotations

from pathlib import Path

import yaml

LEVELS = {"strong", "moderate", "limited", "mixed", "unestablished"}
KINDS = {"research", "framework", "practitioner", "policy"}
REQUIRED = ("title", "kind", "evidence_level", "summary", "limits", "risks", "urls", "checked")


class SourceError(ValueError):
    pass


def load_sources(path: str | Path | None = None) -> dict[str, dict]:
    data = yaml.safe_load((Path(path) if path else Path(__file__).with_name("sources.yaml")).read_text())["sources"]
    for sid, s in data.items():
        missing = [k for k in REQUIRED if k not in s]
        if missing:
            raise SourceError(f"{sid}: missing {missing}")
        if s["evidence_level"] not in LEVELS or s["kind"] not in KINDS:
            raise SourceError(f"{sid}: bad evidence_level or kind")
        if not s["limits"].strip() or not s["risks"].strip():
            raise SourceError(f"{sid}: limits and risks must be stated")
        if s["evidence_level"] in ("strong", "moderate") and not s["urls"] and s["checked"] != "not checked":
            raise SourceError(f"{sid}: claimed as checked but lists no source")
    return data
