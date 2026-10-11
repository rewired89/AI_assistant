"""Look at, correct and delete what the companion remembers. Run it while the companion is stopped.

    python -m tools.memory_review --config config\\companion.yaml list [--pending]
    python -m tools.memory_review --config config\\companion.yaml confirm <id>
    python -m tools.memory_review --config config\\companion.yaml delete <id>
    python -m tools.memory_review --config config\\companion.yaml clear <category> --yes
    python -m tools.memory_review --config config\\companion.yaml export
    python -m tools.memory_review --config config\\companion.yaml vault-status
    python -m tools.memory_review --config config\\companion.yaml vault-clear --yes

The private reflection vault is deliberately NOT readable here: vault-status shows only a count and vault-clear
deletes everything in it. What she reflected on is never shown to whoever runs the host."""
from __future__ import annotations

import argparse
import os
import sys
import time

from companion.app import AppConfig, ConfigProblem
from companion.memory import CryptoError, MemoryError_, MemoryStore, ReflectionVault


def _open(cfg: AppConfig, cls, name: str):
    pw = None
    if cfg.memory.get("encrypt"):
        pw = os.environ.get(cfg.memory["passphrase_env"])
        if not pw:
            raise ConfigProblem(f"set the environment variable {cfg.memory['passphrase_env']} first")
    return cls(cfg.data_dir / name, cfg.user_id, time.time, pw)


def main(argv: list[str] | None = None, out=None) -> int:
    out = out or sys.stdout
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", required=True)
    sub = ap.add_subparsers(dest="cmd", required=True)
    ls = sub.add_parser("list")
    ls.add_argument("--category")
    ls.add_argument("--pending", action="store_true", help="only suggestions waiting for your yes")
    for c in ("confirm", "delete"):
        sub.add_parser(c).add_argument("id")
    cl = sub.add_parser("clear")
    cl.add_argument("category")
    cl.add_argument("--yes", action="store_true")
    sub.add_parser("export")
    sub.add_parser("vault-status")
    vc = sub.add_parser("vault-clear")
    vc.add_argument("--yes", action="store_true")
    a = ap.parse_args(argv)
    try:
        cfg = AppConfig.load(a.config)
        if a.cmd.startswith("vault"):
            v = _open(cfg, ReflectionVault, "reflections.db")
            try:
                if a.cmd == "vault-status":
                    print(f"{len(v.list())} private reflections (contents are not shown)", file=out)
                elif not a.yes:
                    print("this deletes every private reflection and cannot be undone; repeat with --yes", file=out)
                    return 2
                else:
                    print(f"deleted {v.clear('reflection')} reflections", file=out)
            finally:
                v.close()
            return 0
        m = _open(cfg, MemoryStore, "memory.db")
        try:
            if a.cmd == "list":
                for r in m.list(a.category):
                    if a.pending and r["status"] != "pending":
                        continue
                    print(f"{r['id']}  {r['category']:<9} {r['status']:<8} {(r['key'] + ': ') if r['key'] else ''}{r['value']}", file=out)
            elif a.cmd == "confirm":
                m.confirm(a.id)
                print("confirmed", file=out)
            elif a.cmd == "delete":
                m.delete(a.id)
                print("deleted", file=out)
            elif a.cmd == "clear":
                if not a.yes:
                    print("this deletes everything in that category; repeat with --yes", file=out)
                    return 2
                print(f"deleted {m.clear(a.category)} memories", file=out)
            elif a.cmd == "export":
                print(m.export(), file=out)
        finally:
            m.close()
    except (ConfigProblem, MemoryError_, CryptoError, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
