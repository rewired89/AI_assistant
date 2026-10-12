"""Check and verify the Daily Psychology Discoveries library (content/discoveries/*.yaml).

    python -m tools.insights check            # validate every file; lists drafts and what is deliverable
    python -m tools.insights show ID          # print exactly what she would hear, and its length
    python -m tools.insights verify ID --by "Your Name" --basis "opened the paper at <link>; the summary matches its abstract"
    python -m tools.insights unverify ID      # take an entry out of delivery

`verify` refuses an entry that does not pass the content rules. It records who checked, when and how; it cannot make an
unsourced claim true, so do it only after reading the source. Entries marked verified by Claude say 'abstract level':
spot-check them and confirm with --by to mark them owner_confirmed."""
from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

import yaml

from companion.discoveries import load_dir, problems, script_for, seconds_for

LIB = Path(__file__).resolve().parent.parent / "content" / "discoveries"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", default=str(LIB))
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check")
    s = sub.add_parser("show"); s.add_argument("id")
    v = sub.add_parser("verify"); v.add_argument("id"); v.add_argument("--by", required=True); v.add_argument("--basis", required=True)
    u = sub.add_parser("unverify"); u.add_argument("id")
    a = ap.parse_args(argv)
    lib = Path(a.dir)
    entries, bad = load_dir(lib)
    if a.cmd == "check":
        ready = [e for e in entries if e.verified]
        print(f"{len(ready)} deliverable, {len(entries) - len(ready)} drafts, {len(bad)} invalid files")
        for e in entries:
            tag = ("verified, owner-confirmed" if e.owner_confirmed else "verified") if e.verified else "DRAFT"
            print(f"  {e.id:34} {e.topic:28} {tag}")
        for f, p in bad.items():
            print(f"  INVALID {f}: " + "; ".join(p))
        return 1 if bad else 0
    path = lib / f"{a.id}.yaml"
    if not path.exists():
        print(f"no such entry: {a.id}", file=sys.stderr)
        return 1
    d = yaml.safe_load(path.read_text())
    if a.cmd == "show":
        e = next((x for x in entries if x.id == a.id), None)
        if e is None:
            print("; ".join(bad.get(path.name, ["invalid"])), file=sys.stderr)
            return 1
        t = script_for(e)
        print(t, f"\n\n[{seconds_for(t):.0f} s | {e.sources_text()} | {e.source_url}]")
        return 0
    if a.cmd == "unverify":
        d["verification"] = {**d["verification"], "status": "unverified", "owner_confirmed": False}
    else:
        d["verification"] = {"status": "verified", "checked_on": dt.date.today().isoformat(), "checked_by": a.by,
                             "basis": a.basis, "owner_confirmed": True}
        p = problems(d)
        if p:
            print("not verified, fix first: " + "; ".join(p), file=sys.stderr)
            return 1
    path.write_text(yaml.safe_dump(d, sort_keys=False, allow_unicode=True, width=10000))
    print(f"{a.id}: {d['verification']['status']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
