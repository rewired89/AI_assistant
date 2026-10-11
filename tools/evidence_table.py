"""Renders companion/psychology/sources/sources.yaml as docs/psychology-evidence.md (kept in sync by a test).

    python -m tools.evidence_table > docs/psychology-evidence.md
"""
from __future__ import annotations

from companion.psychology.exercises import load_library
from companion.psychology.sources import load_sources


def render() -> str:
    src, lib = load_sources(), load_library()
    used: dict[str, list[str]] = {}
    for eid, ex in lib.items():
        for s in ex["source_or_evidence_basis"]:
            used.setdefault(s, []).append(eid)
    out = ["# Psychology evidence register", "",
           "Generated from `companion/psychology/sources/sources.yaml`. Do not edit by hand.",
           "\"2026-10-11 secondary summaries\" means I read search-result summaries, not the primary papers. "
           "\"not checked\" means written from general knowledge. Evidence level describes this specific use, not the parent therapy.", ""]
    for sid, s in src.items():
        out += [f"## {s['title']}", f"- id: `{sid}` | kind: {s['kind']} | **evidence: {s['evidence_level']}** | checked: {s['checked']}",
                f"- summary: {s['summary']}", f"- limits: {s['limits']}", f"- risks: {s['risks']}",
                f"- used by: {', '.join(f'`{e}`' for e in used.get(sid, [])) or 'safety design only'}"]
        out += [f"- link: {u}" for u in s["urls"]]
        out.append("")
    return "\n".join(out)


if __name__ == "__main__":
    print(render())
