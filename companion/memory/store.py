"""Structured, consent-aware memory on SQLite (stdlib). Local-first, no network.

Two physically separate stores per user:
  MemoryStore    profile / episodic / semantic / goal_habit  (ordinary companion memory)
  ReflectionVault reflection                                  (sensitive; own file; consent per write)
Rules enforced here, not left to callers:
  - AI inferences are never active facts: they sit as `pending` until the user confirms.
  - Sensitive reflections need consent=True on every write and the category enabled (default off).
  - A store file is bound to one user_id; any other id is refused.
  - Hard delete (secure_delete on), per-category clear, export, retention purge.
Optional encryption at rest: pass `passphrase=` (see crypto.py for exactly what is and is not protected).
Not implemented: embeddings (retrieval is lexical).
"""
from __future__ import annotations

import functools
import json
import math
import re
import sqlite3
import threading
import uuid
from pathlib import Path
from typing import Callable

from ..interfaces import MemoryStore as MemoryStoreABC
from .crypto import CHECK_TEXT, CryptoError, FieldCipher

ORDINARY = ("profile", "episodic", "semantic", "goal_habit")
VAULT = ("reflection",)
SOURCES = ("explicit_user_statement", "user_correction", "user_confirmed_inference", "ai_inference")
DEFAULT_RETENTION_DAYS = {"profile": None, "episodic": 90, "semantic": None, "goal_habit": None, "reflection": 60}
MAX_VALUE_CHARS = 1000
MAX_CONTEXT_CHARS = 200
_STOP = set("a an the is are was were of to and or in on at it my i me you your for with that this be".split())
_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS settings(category TEXT PRIMARY KEY, enabled INTEGER NOT NULL, retention_days INTEGER);
CREATE TABLE IF NOT EXISTS memories(
  id TEXT PRIMARY KEY, category TEXT NOT NULL, key TEXT, value TEXT NOT NULL, tags TEXT NOT NULL DEFAULT '[]',
  source TEXT NOT NULL, confidence REAL NOT NULL, status TEXT NOT NULL, source_context TEXT,
  created_at REAL NOT NULL, updated_at REAL NOT NULL, expires_at REAL, key_idx TEXT);
CREATE INDEX IF NOT EXISTS ix_cat ON memories(category, status);
"""


class MemoryError_(Exception):
    pass


class Disabled(MemoryError_):
    """Category is switched off."""


class ConsentRequired(MemoryError_):
    pass


class EncryptionError(MemoryError_):
    pass


class WrongUser(MemoryError_):
    pass


def _tokens(text: str) -> list[str]:
    return [t for t in re.findall(r"[a-z0-9']+", text.lower()) if t not in _STOP]


def _norm(text: str) -> str:
    return " ".join(_tokens(text))


def _jaccard(a: str, b: str) -> float:
    x, y = set(_tokens(a)), set(_tokens(b))
    return len(x & y) / len(x | y) if x and y else 0.0


class _Base(MemoryStoreABC):
    categories: tuple[str, ...] = ()
    default_enabled: dict[str, bool] = {}

    def __init__(self, path: Path | str, user_id: str, clock: Callable[[], float], passphrase: str | None = None):
        self.clock, self.user_id = clock, user_id
        self.cipher: FieldCipher | None = None
        self._lock = threading.RLock()   # the voice pipeline calls memory from worker threads
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA secure_delete=ON")
        self.db.executescript(_SCHEMA)
        if "key_idx" not in {r["name"] for r in self.db.execute("PRAGMA table_info(memories)")}:
            self.db.execute("ALTER TABLE memories ADD COLUMN key_idx TEXT")      # files made before encryption existed
        row = self.db.execute("SELECT v FROM meta WHERE k='user_id'").fetchone()
        if row is None:
            self.db.execute("INSERT INTO meta VALUES('user_id', ?)", (user_id,))
        elif row["v"] != user_id:
            self.db.close()
            raise WrongUser("this memory file belongs to a different user")
        try:
            self._setup_encryption(passphrase)
        except Exception:
            self.db.close()
            raise
        for c in self.categories:
            self.db.execute("INSERT OR IGNORE INTO settings VALUES(?,?,?)",
                            (c, int(self.default_enabled.get(c, True)), DEFAULT_RETENTION_DAYS[c]))
        self.db.commit()

    def _meta(self, k: str) -> str | None:
        r = self.db.execute("SELECT v FROM meta WHERE k=?", (k,)).fetchone()
        return r["v"] if r else None

    def _setup_encryption(self, passphrase: str | None) -> None:
        mode = self._meta("enc") or "none"
        if mode == "none" and passphrase is None:
            return
        if mode == "none":
            if self.db.execute("SELECT COUNT(*) FROM memories").fetchone()[0]:
                raise EncryptionError("this file holds unencrypted memories; encrypt it first (python -m tools.memory_crypt)")
            salt = FieldCipher.new_salt()
            self.cipher = FieldCipher(passphrase, salt)
            self.db.execute("INSERT OR REPLACE INTO meta VALUES('enc', 'aesgcm-v1')")
            self.db.execute("INSERT OR REPLACE INTO meta VALUES('enc_salt', ?)", (salt.hex(),))
            self.db.execute("INSERT OR REPLACE INTO meta VALUES('enc_check', ?)", (self.cipher.seal(CHECK_TEXT, self._aad("meta", "check")),))
            return
        if passphrase is None:
            raise EncryptionError("this memory file is encrypted; a passphrase is required")
        self.cipher = FieldCipher(passphrase, bytes.fromhex(self._meta("enc_salt")))
        try:
            if self.cipher.open(self._meta("enc_check"), self._aad("meta", "check")) != CHECK_TEXT:
                raise CryptoError("bad check")
        except CryptoError:
            raise EncryptionError("wrong passphrase") from None

    def _aad(self, mid: str, field: str) -> str:
        return f"{self.user_id}|{mid}|{field}"

    def _seal(self, mid: str, field: str, text: str | None) -> str | None:
        return self.cipher.seal(text, self._aad(mid, field)) if self.cipher else text

    def _unseal_row(self, r) -> dict:
        d = dict(r)
        if self.cipher:
            for f in ("key", "value", "tags", "source_context"):
                d[f] = self.cipher.open(d[f], self._aad(d["id"], f))
        d.pop("key_idx", None)
        return d

    def _key_idx(self, cat: str, key: str | None) -> str | None:
        return self.cipher.index(cat, key) if self.cipher and key else None

    def close(self) -> None:
        self.db.close()

    # ---- settings -------------------------------------------------------
    def set_enabled(self, category: str, enabled: bool) -> None:
        self._check_cat(category)
        self.db.execute("UPDATE settings SET enabled=? WHERE category=?", (int(enabled), category))
        self.db.commit()

    def set_retention(self, category: str, days: int | None) -> None:
        self._check_cat(category)
        if days is not None and days < 1:
            raise MemoryError_("retention must be >= 1 day or None")
        self.db.execute("UPDATE settings SET retention_days=? WHERE category=?", (days, category))
        self.db.commit()

    def settings(self) -> dict:
        return {r["category"]: {"enabled": bool(r["enabled"]), "retention_days": r["retention_days"]}
                for r in self.db.execute("SELECT * FROM settings")}

    def _check_cat(self, category: str) -> None:
        if category not in self.categories:
            raise MemoryError_(f"unknown category {category!r} for this store")

    # ---- write ----------------------------------------------------------
    def _guard_add(self, rec: dict, consent: bool) -> None:
        """Hook for subclasses."""

    def add(self, record: dict, consent: bool = False) -> str:
        cat = record.get("category")
        self._check_cat(cat)
        value = str(record.get("value", "")).strip()
        if not value or len(value) > MAX_VALUE_CHARS:
            raise MemoryError_("value must be 1..%d chars" % MAX_VALUE_CHARS)
        source = record.get("source", "explicit_user_statement")
        if source not in SOURCES:
            raise MemoryError_(f"unknown source {source!r}")
        conf = float(record.get("confidence", 1.0))
        if not 0 <= conf <= 1:
            raise MemoryError_("confidence out of range")
        s = self.settings()[cat]
        if not s["enabled"]:
            raise Disabled(cat)
        self._guard_add(record, consent)
        status = "pending" if source == "ai_inference" else "active"
        now = self.clock()
        key = record.get("key")
        ctx = (record.get("source_context") or "")[:MAX_CONTEXT_CHARS] or None
        dup = self._find_duplicate(cat, key, value)
        tags_json = json.dumps(record.get("tags", []))
        expires = now + s["retention_days"] * 86400 if s["retention_days"] else None
        if dup and source == "ai_inference":
            return dup["id"]  # a model suggestion never modifies or re-activates an existing memory
        if dup:
            keep_status = "active" if dup["status"] == "active" else status
            self.db.execute(
                "UPDATE memories SET value=?, confidence=MAX(confidence,?), updated_at=?, expires_at=?, status=?,"
                " source=? WHERE id=?",
                (self._seal(dup["id"], "value", value if key else dup["value"]), conf, now, expires, keep_status, source, dup["id"]))
            self.db.commit()
            return dup["id"]
        mid = uuid.uuid4().hex
        self.db.execute("INSERT INTO memories VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (mid, cat, self._seal(mid, "key", key), self._seal(mid, "value", value),
                         self._seal(mid, "tags", tags_json), source, conf, status,
                         self._seal(mid, "source_context", ctx), now, now, expires, self._key_idx(cat, key)))
        self.db.commit()
        return mid

    def _find_duplicate(self, cat: str, key: str | None, value: str):
        if key:
            if self.cipher:
                r = self.db.execute("SELECT * FROM memories WHERE category=? AND key_idx=?", (cat, self._key_idx(cat, key))).fetchone()
            else:
                r = self.db.execute("SELECT * FROM memories WHERE category=? AND key=?", (cat, key)).fetchone()
            return self._unseal_row(r) if r else None
        q = "SELECT * FROM memories WHERE category=? AND " + ("key_idx IS NULL" if self.cipher else "key IS NULL")
        for raw in self.db.execute(q, (cat,)).fetchall():
            r = self._unseal_row(raw)
            if _norm(r["value"]) == _norm(value) or _jaccard(r["value"], value) >= 0.85:
                return r
        return None

    def confirm(self, memory_id: str) -> None:
        """User approved an AI-suggested memory. Only now does it become active."""
        cur = self.db.execute(
            "UPDATE memories SET status='active', source='user_confirmed_inference', updated_at=? "
            "WHERE id=? AND status='pending'", (self.clock(), memory_id))
        if cur.rowcount == 0:
            raise MemoryError_("no pending memory with that id")
        self.db.commit()

    def update(self, memory_id: str, changes: dict) -> None:
        allowed = {"value", "key", "tags"}
        if not changes or set(changes) - allowed:
            raise MemoryError_(f"can only change {sorted(allowed)}")
        row = self._get(memory_id)
        value = str(changes.get("value", row["value"])).strip()
        if not value or len(value) > MAX_VALUE_CHARS:
            raise MemoryError_("value must be 1..%d chars" % MAX_VALUE_CHARS)
        key = changes.get("key", row["key"])
        self.db.execute("UPDATE memories SET value=?, key=?, key_idx=?, tags=?, source='user_correction', status='active',"
                        " confidence=1.0, updated_at=? WHERE id=?",
                        (self._seal(memory_id, "value", value), self._seal(memory_id, "key", key),
                         self._key_idx(row["category"], key),
                         self._seal(memory_id, "tags", json.dumps(changes.get("tags", json.loads(row["tags"])))),
                         self.clock(), memory_id))
        self.db.commit()

    def delete(self, memory_id: str) -> None:
        self._get(memory_id)
        self.db.execute("DELETE FROM memories WHERE id=?", (memory_id,))
        self.db.commit()

    def clear(self, category: str) -> int:
        self._check_cat(category)
        n = self.db.execute("DELETE FROM memories WHERE category=?", (category,)).rowcount
        self.db.commit()
        return n

    def purge_expired(self) -> int:
        n = self.db.execute("DELETE FROM memories WHERE expires_at IS NOT NULL AND expires_at<=?",
                            (self.clock(),)).rowcount
        self.db.commit()
        return n

    def _get(self, memory_id: str):
        row = self.db.execute("SELECT * FROM memories WHERE id=?", (memory_id,)).fetchone()
        if row is None:
            raise MemoryError_("no such memory")
        return self._unseal_row(row)

    # ---- read -----------------------------------------------------------
    def _out(self, r) -> dict:
        d = self._unseal_row(r) if not isinstance(r, dict) else dict(r)
        d["tags"] = json.loads(d["tags"])
        return d

    def list(self, category: str | None = None, include_pending: bool = True) -> list[dict]:
        """Everything the user may inspect, including pending suggestions."""
        q, args = "SELECT * FROM memories WHERE 1=1", []
        if category:
            self._check_cat(category); q += " AND category=?"; args.append(category)
        if not include_pending:
            q += " AND status='active'"
        return [self._out(r) for r in self.db.execute(q + " ORDER BY created_at", args)]

    def search(self, query: str, kinds: list[str] | None = None, limit: int = 5) -> list[dict]:
        """Active, enabled, unexpired memories ranked by lexical relevance x confidence x recency.
        Pending (unconfirmed) items never appear here."""
        self.purge_expired()
        enabled = [c for c, s in self.settings().items() if s["enabled"] and (not kinds or c in kinds)]
        q = set(_tokens(query))
        if not q or not enabled:
            return []
        now, scored = self.clock(), []
        marks = ",".join("?" * len(enabled))
        for raw in self.db.execute(f"SELECT * FROM memories WHERE status='active' AND category IN ({marks})", enabled).fetchall():
            r = self._unseal_row(raw)
            doc = set(_tokens(f"{r['key'] or ''} {r['value']} {' '.join(json.loads(r['tags']))}"))
            overlap = len(q & doc)
            if not overlap:
                continue
            rel = overlap / math.sqrt(len(q) * max(len(doc), 1))
            age_days = max(0.0, (now - r["updated_at"]) / 86400)
            score = rel * r["confidence"] * (0.5 + 0.5 * 0.5 ** (age_days / 60))
            scored.append((score, r))
        scored.sort(key=lambda t: -t[0])
        return [dict(self._out(r), score=round(s, 4)) for s, r in scored[:limit]]

    def get(self, memory_id: str) -> dict | None:
        """One active memory by id, or None (missing, deleted, pending, disabled category, or expired)."""
        row = self.db.execute("SELECT * FROM memories WHERE id=? AND status='active'", (memory_id,)).fetchone()
        if row is None or not self.settings()[row["category"]]["enabled"]:
            return None
        if row["expires_at"] is not None and row["expires_at"] <= self.clock():
            return None
        return self._out(row)

    def export(self) -> str:
        return json.dumps({"user_id": self.user_id, "exported_at": self.clock(), "settings": self.settings(),
                           "memories": self.list()}, indent=2, sort_keys=True)


def _locked(fn):
    @functools.wraps(fn)
    def wrapper(self, *a, **k):
        with self._lock:
            return fn(self, *a, **k)
    return wrapper


for _name in ("set_enabled", "set_retention", "settings", "add", "confirm", "update", "delete", "clear",
              "purge_expired", "list", "search", "export", "get"):
    setattr(_Base, _name, _locked(getattr(_Base, _name)))


class MemoryStore(_Base):
    categories = ORDINARY


class ReflectionVault(_Base):
    """Sensitive reflections. Separate file, off by default, consent on every write,
    and AI inferences are refused outright (no psychological interpretation stored as a fact)."""
    categories = VAULT
    default_enabled = {"reflection": False}

    def _guard_add(self, rec: dict, consent: bool) -> None:
        if not consent:
            raise ConsentRequired("ask the user before saving a reflection")
        if rec.get("source") == "ai_inference":
            raise MemoryError_("AI interpretations are not stored in the reflection vault")


def encrypt_existing(path: Path | str, user_id: str, passphrase: str, clock: Callable[[], float] = lambda: 0.0) -> int:
    """Converts a plaintext memory file to an encrypted one. Works on a copy, checks it by reading it back with the
    passphrase, then swaps it in. Returns the number of memories converted. The old plaintext file is deleted but its
    disk blocks are not scrubbed: do this on a disk you trust, and treat older backups as plaintext."""
    import os
    import shutil
    path = Path(path)
    tmp = path.with_suffix(".enc-tmp")
    shutil.copyfile(path, tmp)
    try:
        db = sqlite3.connect(str(tmp))
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA secure_delete=ON")
        if (db.execute("SELECT v FROM meta WHERE k='enc'").fetchone() or ["none"])[0] != "none":
            raise EncryptionError("this file is already encrypted")
        owner = db.execute("SELECT v FROM meta WHERE k='user_id'").fetchone()
        if owner is None or owner["v"] != user_id:
            raise WrongUser("this memory file belongs to a different user")
        if "key_idx" not in {r["name"] for r in db.execute("PRAGMA table_info(memories)")}:
            db.execute("ALTER TABLE memories ADD COLUMN key_idx TEXT")
        salt = FieldCipher.new_salt()
        c = FieldCipher(passphrase, salt)
        rows = db.execute("SELECT * FROM memories").fetchall()
        aad = lambda mid, f: f"{user_id}|{mid}|{f}"  # noqa: E731
        for r in rows:
            db.execute("UPDATE memories SET key=?, value=?, tags=?, source_context=?, key_idx=? WHERE id=?",
                       (c.seal(r["key"], aad(r["id"], "key")), c.seal(r["value"], aad(r["id"], "value")),
                        c.seal(r["tags"], aad(r["id"], "tags")), c.seal(r["source_context"], aad(r["id"], "source_context")),
                        c.index(r["category"], r["key"]) if r["key"] else None, r["id"]))
        db.execute("INSERT OR REPLACE INTO meta VALUES('enc', 'aesgcm-v1')")
        db.execute("INSERT OR REPLACE INTO meta VALUES('enc_salt', ?)", (salt.hex(),))
        db.execute("INSERT OR REPLACE INTO meta VALUES('enc_check', ?)", (c.seal(CHECK_TEXT, aad("meta", "check")),))
        db.commit()
        db.execute("VACUUM")
        db.close()
        before = _plain_rows(path)

        probe = type("Probe", (_Base,), {"categories": tuple({r["category"] for r in rows}) or ("profile",),
                                         "default_enabled": {}})
        p = probe(tmp, user_id, clock, passphrase)
        try:
            after = [(m["id"], m["category"], m["key"], m["value"], tuple(m["tags"]), m["source_context"]) for m in p.list()]
        finally:
            p.close()
        if sorted(before) != sorted(after):
            raise EncryptionError("verification failed; the original file was left untouched")
        os.replace(tmp, path)
        return len(rows)
    finally:
        if tmp.exists():
            tmp.unlink()


def _plain_rows(path: Path) -> list[tuple]:
    db = sqlite3.connect(str(path))
    try:
        return [(i, c, k, v, tuple(json.loads(t)), x) for i, c, k, v, t, x in
                db.execute("SELECT id, category, key, value, tags, source_context FROM memories")]
    finally:
        db.close()
