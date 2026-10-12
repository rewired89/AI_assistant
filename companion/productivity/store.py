"""Her notes and lists: one small SQLite file shared by the robot's voice commands and the phone app (so they are always in sync).

Notes: kind is 'voice' (spoken to the robot over several turns), 'dictated' (one spoken sentence or paragraph) or 'written' (typed on
the phone). The words are kept exactly as transcribed unless she chose light cleanup; `cleaned` says which. Lists hold items that are
only ever completed by an explicit action, never because she talked about the task.

Optional encryption at rest (title, body, tags, list names and item text) with the same field cipher as the memory files:
it protects a stolen disk or backup, NOT someone using the running host. Audio is not stored yet (`audio_path` is reserved).
All methods are thread-safe: the phone app's server threads and the robot's voice thread share one store."""
from __future__ import annotations

import json
import re
import sqlite3
import threading
import uuid
from pathlib import Path

from ..memory.crypto import CHECK_TEXT, CryptoError, FieldCipher

KINDS = ("voice", "dictated", "written")
TRANSCRIPT = ("done", "pending", "failed", "n/a")
MAX_BODY = 100_000
MAX_TITLE = 120
MAX_TAGS = 12
MAX_ITEM = 300
MAX_ITEMS_PER_LIST = 500
MAX_LISTS = 100
MAX_NOTES = 20_000
_WORD = re.compile(r"[a-z0-9']+")
STOP = {"the", "a", "an", "of", "to", "my", "and", "in", "on", "for", "about", "i", "it", "is", "that", "this", "with", "note", "notes"}


class StoreError(Exception):
    pass


def tokens(text: str) -> list[str]:
    return [w for w in _WORD.findall(text.lower()) if w not in STOP and len(w) > 1]


def suggest_title(body: str, words: int = 6) -> str:
    first = re.split(r"(?<=[.!?])\s+|\n", body.strip(), maxsplit=1)[0]
    w = first.split()[:words]
    t = " ".join(w).strip(" ,.;:!?-") or "Untitled note"
    return (t[:1].upper() + t[1:])[:MAX_TITLE]


def light_cleanup(text: str) -> str:
    """Spelling-neutral tidy-up only: drop filler sounds, fix spacing, capitalise sentence starts, end with a full stop.
    Never rewords, reorders or summarises."""
    t = re.sub(r"\b(um+|uh+|er+|erm|you know,)\b[,]?\s*", "", text, flags=re.I)
    t = re.sub(r"\s+", " ", t).strip()
    t = re.sub(r"\s+([,.!?;:])", r"\1", t)
    t = re.sub(r"(^|[.!?]\s+)([a-z])", lambda m: m.group(1) + m.group(2).upper(), t)
    if t and t[-1] not in ".!?":
        t += "."
    return t


class ProductivityStore:
    def __init__(self, path: str | Path, clock, passphrase: str | None = None):
        self.path, self.clock = Path(path), clock
        self._lock = threading.RLock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(self.path), check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self._cipher: FieldCipher | None = None
        with self._lock:
            self.db.executescript("""
            CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS notes (id TEXT PRIMARY KEY, kind TEXT NOT NULL, title TEXT NOT NULL, body TEXT NOT NULL,
              tags TEXT NOT NULL, archived INTEGER NOT NULL DEFAULT 0, created_at REAL NOT NULL, modified_at REAL NOT NULL,
              transcript_state TEXT NOT NULL DEFAULT 'n/a', audio_path TEXT, cleaned INTEGER NOT NULL DEFAULT 0, source TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS lists (id TEXT PRIMARY KEY, name TEXT NOT NULL, position INTEGER NOT NULL,
              archived INTEGER NOT NULL DEFAULT 0, created_at REAL NOT NULL, modified_at REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS items (id TEXT PRIMARY KEY, list_id TEXT NOT NULL REFERENCES lists(id) ON DELETE CASCADE,
              text TEXT NOT NULL, done INTEGER NOT NULL DEFAULT 0, position INTEGER NOT NULL, due_at REAL, created_at REAL NOT NULL,
              completed_at REAL);
            CREATE INDEX IF NOT EXISTS idx_items_list ON items(list_id);
            PRAGMA foreign_keys=ON;""")
            self._init_cipher(passphrase)

    # ---- encryption -----------------------------------------------------------------------------
    def _meta(self, k: str) -> str | None:
        r = self.db.execute("SELECT v FROM meta WHERE k=?", (k,)).fetchone()
        return r["v"] if r else None

    def _set_meta(self, k: str, v: str) -> None:
        self.db.execute("INSERT INTO meta(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, v))
        self.db.commit()

    def _init_cipher(self, passphrase: str | None) -> None:
        salt_hex, check = self._meta("salt"), self._meta("check")
        if passphrase is None:
            if salt_hex:
                raise StoreError("these notes are encrypted; a passphrase is required")
            return
        salt = bytes.fromhex(salt_hex) if salt_hex else FieldCipher.new_salt()
        try:
            self._cipher = FieldCipher(passphrase, salt)
        except CryptoError as e:
            raise StoreError(str(e)) from None
        if check:
            try:
                if self._cipher.open(check, "check") != CHECK_TEXT:
                    raise CryptoError("bad check")
            except CryptoError:
                raise StoreError("wrong passphrase for the notes file") from None
        else:
            if self.db.execute("SELECT 1 FROM notes LIMIT 1").fetchone() or self.db.execute("SELECT 1 FROM lists LIMIT 1").fetchone():
                raise StoreError("these notes are not encrypted yet; use encrypt_existing()")
            self._set_meta("salt", salt.hex())
            self._set_meta("check", self._cipher.seal(CHECK_TEXT, "check"))

    def _seal(self, text: str | None, aad: str) -> str | None:
        return self._cipher.seal(text, aad) if self._cipher else text

    def _open(self, blob: str | None, aad: str) -> str | None:
        if self._cipher is None or blob is None:
            return blob
        try:
            return self._cipher.open(blob, aad)
        except CryptoError:
            raise StoreError("a stored field failed its integrity check") from None

    @property
    def encrypted(self) -> bool:
        return self._cipher is not None

    def close(self) -> None:
        with self._lock:
            self.db.close()

    # ---- settings (not secret: just her preferences) --------------------------------------------
    def setting(self, key: str, default: str = "") -> str:
        with self._lock:
            return self._meta("s_" + key) or default

    def set_setting(self, key: str, value: str) -> None:
        with self._lock:
            self._set_meta("s_" + key, value[:200])

    # ---- notes ----------------------------------------------------------------------------------
    def _note(self, r: sqlite3.Row) -> dict:
        i = r["id"]
        return {"id": i, "kind": r["kind"], "title": self._open(r["title"], f"n:{i}:title"), "body": self._open(r["body"], f"n:{i}:body"),
                "tags": json.loads(self._open(r["tags"], f"n:{i}:tags")), "archived": bool(r["archived"]),
                "created_at": r["created_at"], "modified_at": r["modified_at"], "transcript_state": r["transcript_state"],
                "audio": bool(r["audio_path"]), "cleaned": bool(r["cleaned"]), "source": r["source"]}

    @staticmethod
    def _clean_tags(tags) -> list[str]:
        out = []
        for t in tags or []:
            t = re.sub(r"\s+", " ", str(t)).strip().lstrip("#").lower()[:30]
            if t and t not in out:
                out.append(t)
        return out[:MAX_TAGS]

    def add_note(self, body: str, kind: str = "written", title: str | None = None, tags=None, source: str = "phone",
                 cleaned: bool = False, transcript_state: str = "n/a") -> dict:
        if kind not in KINDS or transcript_state not in TRANSCRIPT:
            raise StoreError("bad note kind or transcript state")
        body = (body or "").strip()
        if not body:
            raise StoreError("a note needs some text")
        if len(body) > MAX_BODY:
            raise StoreError(f"that note is too long (limit {MAX_BODY} characters)")
        with self._lock:
            if self.db.execute("SELECT COUNT(*) FROM notes").fetchone()[0] >= MAX_NOTES:
                raise StoreError("the notes file is full")
            i, now = uuid.uuid4().hex[:12], self.clock()
            title = (title or "").strip()[:MAX_TITLE] or suggest_title(body)
            self.db.execute("INSERT INTO notes(id,kind,title,body,tags,created_at,modified_at,transcript_state,cleaned,source) VALUES(?,?,?,?,?,?,?,?,?,?)",
                            (i, kind, self._seal(title, f"n:{i}:title"), self._seal(body, f"n:{i}:body"),
                             self._seal(json.dumps(self._clean_tags(tags)), f"n:{i}:tags"), now, now, transcript_state, int(cleaned), source))
            self.db.commit()
            return self.get_note(i)

    def get_note(self, note_id: str) -> dict | None:
        with self._lock:
            r = self.db.execute("SELECT * FROM notes WHERE id=?", (note_id,)).fetchone()
            return self._note(r) if r else None

    def update_note(self, note_id: str, title: str | None = None, body: str | None = None, tags=None, archived: bool | None = None,
                    append: str | None = None) -> dict:
        with self._lock:
            n = self.get_note(note_id)
            if n is None:
                raise StoreError("no such note")
            if append is not None:
                body = n["body"] + ("\n" if n["body"] and not n["body"].endswith("\n") else "") + append.strip()
            if body is not None:
                body = body.strip()
                if not body or len(body) > MAX_BODY:
                    raise StoreError("a note needs some text, and no more than %d characters" % MAX_BODY)
            title = n["title"] if title is None else (title.strip()[:MAX_TITLE] or n["title"])
            body = n["body"] if body is None else body
            tg = n["tags"] if tags is None else self._clean_tags(tags)
            self.db.execute("UPDATE notes SET title=?, body=?, tags=?, archived=?, modified_at=? WHERE id=?",
                            (self._seal(title, f"n:{note_id}:title"), self._seal(body, f"n:{note_id}:body"),
                             self._seal(json.dumps(tg), f"n:{note_id}:tags"), int(n["archived"] if archived is None else archived),
                             self.clock(), note_id))
            self.db.commit()
            return self.get_note(note_id)

    def delete_note(self, note_id: str) -> bool:
        with self._lock:
            r = self.db.execute("SELECT audio_path FROM notes WHERE id=?", (note_id,)).fetchone()
            if r is None:
                return False
            if r["audio_path"]:
                Path(r["audio_path"]).unlink(missing_ok=True)       # the recording goes with the note
            self.db.execute("DELETE FROM notes WHERE id=?", (note_id,))
            self.db.commit()
            return True

    def notes(self, query: str = "", kind: str | None = None, archived: bool = False, since: float | None = None,
              until: float | None = None, limit: int = 100) -> list[dict]:
        """Newest first. With a query: ranked by how many of her words match the title, body or tags."""
        with self._lock:
            rows = [self._note(r) for r in self.db.execute("SELECT * FROM notes WHERE archived=? ORDER BY created_at DESC", (int(archived),))]
        out = []
        q = set(tokens(query))
        for n in rows:
            if kind and n["kind"] != kind:
                continue
            if since is not None and n["created_at"] < since:
                continue
            if until is not None and n["created_at"] >= until:
                continue
            if q:
                doc = set(tokens(f"{n['title']} {n['body']} {' '.join(n['tags'])}"))
                hit = len(q & doc)
                if not hit:
                    continue
                out.append((hit / len(q), n))
            else:
                out.append((0, n))
        out.sort(key=lambda t: (-t[0], -t[1]["created_at"]))
        return [n for _, n in out[:limit]]

    def last_note(self) -> dict | None:
        r = self.notes(limit=1)
        return r[0] if r else None

    # ---- lists ----------------------------------------------------------------------------------
    def _list(self, r: sqlite3.Row, items: bool = True) -> dict:
        i = r["id"]
        d = {"id": i, "name": self._open(r["name"], f"l:{i}:name"), "position": r["position"], "archived": bool(r["archived"]),
             "created_at": r["created_at"], "modified_at": r["modified_at"]}
        its = [self._item(x) for x in self.db.execute("SELECT * FROM items WHERE list_id=? ORDER BY position, created_at", (i,))]
        d["total"], d["done"] = len(its), sum(1 for x in its if x["done"])
        if items:
            d["items"] = its
        return d

    def _item(self, r: sqlite3.Row) -> dict:
        return {"id": r["id"], "list_id": r["list_id"], "text": self._open(r["text"], f"i:{r['id']}:text"), "done": bool(r["done"]),
                "position": r["position"], "due_at": r["due_at"], "created_at": r["created_at"], "completed_at": r["completed_at"]}

    def create_list(self, name: str, items: list[str] | None = None) -> dict:
        name = re.sub(r"\s+", " ", (name or "")).strip()[:60]
        if not name:
            raise StoreError("a list needs a name")
        with self._lock:
            if self.find_list(name, exact=True):
                raise StoreError("you already have a list with that name")
            if self.db.execute("SELECT COUNT(*) FROM lists").fetchone()[0] >= MAX_LISTS:
                raise StoreError("too many lists")
            i, now = uuid.uuid4().hex[:12], self.clock()
            pos = (self.db.execute("SELECT COALESCE(MAX(position),-1)+1 FROM lists").fetchone()[0])
            self.db.execute("INSERT INTO lists(id,name,position,created_at,modified_at) VALUES(?,?,?,?,?)",
                            (i, self._seal(name, f"l:{i}:name"), pos, now, now))
            self.db.commit()
            for t in items or []:
                self.add_item(i, t)
            return self.get_list(i)

    def lists(self, archived: bool = False) -> list[dict]:
        with self._lock:
            return [self._list(r, items=False) for r in self.db.execute("SELECT * FROM lists WHERE archived=? ORDER BY position", (int(archived),))]

    def get_list(self, list_id: str) -> dict | None:
        with self._lock:
            r = self.db.execute("SELECT * FROM lists WHERE id=?", (list_id,)).fetchone()
            return self._list(r) if r else None

    def find_list(self, name: str, exact: bool = False) -> list[dict]:
        """Lists whose name matches her words (every word she said appears in the name). exact=True: same name, ignoring case."""
        q = tokens(name)
        norm = re.sub(r"\s+", " ", name).strip().lower()
        out = []
        for l in self.lists():
            ln = l["name"].lower()
            if exact:
                if ln == norm:
                    out.append(l)
            elif q and set(q) <= set(tokens(ln)):
                out.append(l)
        return out

    def rename_list(self, list_id: str, name: str) -> dict:
        name = re.sub(r"\s+", " ", name).strip()[:60]
        with self._lock:
            if not name or self.get_list(list_id) is None:
                raise StoreError("bad list or name")
            self.db.execute("UPDATE lists SET name=?, modified_at=? WHERE id=?", (self._seal(name, f"l:{list_id}:name"), self.clock(), list_id))
            self.db.commit()
            return self.get_list(list_id)

    def delete_list(self, list_id: str) -> bool:
        with self._lock:
            self.db.execute("DELETE FROM items WHERE list_id=?", (list_id,))
            n = self.db.execute("DELETE FROM lists WHERE id=?", (list_id,)).rowcount
            self.db.commit()
            return bool(n)

    def move_list(self, list_id: str, direction: int) -> None:
        with self._lock:
            ls = self.lists()
            ids = [l["id"] for l in ls]
            if list_id not in ids:
                raise StoreError("no such list")
            i = ids.index(list_id)
            j = max(0, min(len(ids) - 1, i + (1 if direction > 0 else -1)))
            ids[i], ids[j] = ids[j], ids[i]
            for pos, x in enumerate(ids):
                self.db.execute("UPDATE lists SET position=? WHERE id=?", (pos, x))
            self.db.commit()

    # ---- items ----------------------------------------------------------------------------------
    def add_item(self, list_id: str, text: str, due_at: float | None = None) -> dict:
        text = re.sub(r"\s+", " ", (text or "")).strip()[:MAX_ITEM]
        with self._lock:
            if not text:
                raise StoreError("an item needs some text")
            if self.db.execute("SELECT 1 FROM lists WHERE id=?", (list_id,)).fetchone() is None:
                raise StoreError("no such list")
            if self.db.execute("SELECT COUNT(*) FROM items WHERE list_id=?", (list_id,)).fetchone()[0] >= MAX_ITEMS_PER_LIST:
                raise StoreError("that list is full")
            i, now = uuid.uuid4().hex[:12], self.clock()
            pos = self.db.execute("SELECT COALESCE(MAX(position),-1)+1 FROM items WHERE list_id=?", (list_id,)).fetchone()[0]
            self.db.execute("INSERT INTO items(id,list_id,text,position,due_at,created_at) VALUES(?,?,?,?,?,?)",
                            (i, list_id, self._seal(text, f"i:{i}:text"), pos, due_at, now))
            self.db.execute("UPDATE lists SET modified_at=? WHERE id=?", (now, list_id))
            self.db.commit()
            return self._item(self.db.execute("SELECT * FROM items WHERE id=?", (i,)).fetchone())

    def get_item(self, item_id: str) -> dict | None:
        with self._lock:
            r = self.db.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone()
            return self._item(r) if r else None

    def update_item(self, item_id: str, text: str | None = None, done: bool | None = None, due_at: float | None | str = "keep") -> dict:
        with self._lock:
            it = self.get_item(item_id)
            if it is None:
                raise StoreError("no such item")
            text = it["text"] if text is None else re.sub(r"\s+", " ", text).strip()[:MAX_ITEM]
            if not text:
                raise StoreError("an item needs some text")
            now = self.clock()
            d = it["done"] if done is None else bool(done)
            completed = it["completed_at"] if d == it["done"] else (now if d else None)     # restoring clears the completion time
            due = it["due_at"] if due_at == "keep" else due_at
            self.db.execute("UPDATE items SET text=?, done=?, completed_at=?, due_at=? WHERE id=?",
                            (self._seal(text, f"i:{item_id}:text"), int(d), completed, due, item_id))
            self.db.execute("UPDATE lists SET modified_at=? WHERE id=?", (now, it["list_id"]))
            self.db.commit()
            return self.get_item(item_id)

    def delete_item(self, item_id: str) -> bool:
        with self._lock:
            n = self.db.execute("DELETE FROM items WHERE id=?", (item_id,)).rowcount
            self.db.commit()
            return bool(n)

    def move_item(self, item_id: str, direction: int) -> None:
        with self._lock:
            it = self.get_item(item_id)
            if it is None:
                raise StoreError("no such item")
            rows = [r["id"] for r in self.db.execute("SELECT id FROM items WHERE list_id=? ORDER BY position, created_at", (it["list_id"],))]
            i = rows.index(item_id)
            j = max(0, min(len(rows) - 1, i + (1 if direction > 0 else -1)))
            rows[i], rows[j] = rows[j], rows[i]
            for pos, x in enumerate(rows):
                self.db.execute("UPDATE items SET position=? WHERE id=?", (pos, x))
            self.db.commit()

    def find_items(self, text: str, list_id: str | None = None, only_open: bool = False) -> list[dict]:
        q = set(tokens(text))
        out = []
        with self._lock:
            lists = [self.get_list(list_id)] if list_id else [self.get_list(l["id"]) for l in self.lists()]
        for l in filter(None, lists):
            for it in l["items"]:
                if only_open and it["done"]:
                    continue
                if q and q <= set(tokens(it["text"])):
                    out.append({**it, "list_name": l["name"]})
        return out

    # ---- export and deletion ----------------------------------------------------------------------
    def export(self) -> dict:
        with self._lock:
            return {"exported_at": self.clock(), "encrypted_at_rest": self.encrypted,
                    "notes": self.notes(archived=False, limit=10 ** 6) + self.notes(archived=True, limit=10 ** 6),
                    "lists": [self.get_list(l["id"]) for l in self.lists(False) + self.lists(True)]}

    def delete_everything(self) -> dict:
        with self._lock:
            audio = [r["audio_path"] for r in self.db.execute("SELECT audio_path FROM notes WHERE audio_path IS NOT NULL")]
            for a in audio:
                Path(a).unlink(missing_ok=True)
            n = self.db.execute("SELECT COUNT(*) FROM notes").fetchone()[0]
            l = self.db.execute("SELECT COUNT(*) FROM lists").fetchone()[0]
            rem = 0
            try:
                rem = self.db.execute("SELECT COUNT(*) FROM reminders").fetchone()[0]
                self.db.execute("DELETE FROM occurrences")
                self.db.execute("DELETE FROM reminders")
            except sqlite3.OperationalError:
                pass                                                  # reminders were never used
            self.db.execute("DELETE FROM items")
            self.db.execute("DELETE FROM lists")
            self.db.execute("DELETE FROM notes")
            self.db.commit()
            return {"notes": n, "lists": l, "reminders": rem}
