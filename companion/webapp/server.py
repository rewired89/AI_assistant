"""The phone app's server: her notes, lists and private journal, in a web app she opens on her phone (installable from the browser).

Security model (each point has a test):
- Only she gets in: first-time setup needs a one-time pairing code printed on the host, then she chooses her own passphrase
  (scrypt-hashed, never stored plain). Login is rate limited; sessions are random tokens in an HttpOnly SameSite=Strict cookie
  (Secure when served over TLS) and expire when idle.
- Every write needs a custom header and a same-origin Origin; the Host header must be one we serve (blocks DNS rebinding);
  bodies are size limited; everything is JSON, nothing is rendered from her text on the server.
- Without TLS it only listens on this computer (localhost). On the home network it needs the TLS certificate.
- The journal is read from the private vault: she can read and delete entries here, never anyone else.
Honest limits: this is not end-to-end encryption. Whoever administers the host computer can read the files (the data may be encrypted at rest
with a passphrase kept in the host's configuration, which protects a stolen disk, not the administrator)."""
from __future__ import annotations

import hashlib
import hmac
import json
import mimetypes
import os
import re
import secrets
import ssl
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from ..productivity.store import ProductivityStore, StoreError

STATIC = Path(__file__).parent / "static"
MAX_BODY = 256 * 1024
SESSION_IDLE_S = 12 * 3600.0
PAIRING_TTL_S = 3600.0
LOGIN_FAILS = 5
LOGIN_WINDOW_S = 300.0
MIN_PASSPHRASE = 8
STATIC_FILES = {"/": "index.html", "/index.html": "index.html", "/app.css": "app.css", "/app.js": "app.js", "/sw.js": "sw.js",
                "/manifest.webmanifest": "manifest.webmanifest", "/icon.svg": "icon.svg"}
SCRYPT = dict(n=2 ** 14, r=8, p=1, dklen=32)


class WebAppError(Exception):
    pass


# ------------------------------------------------------------------------------------------------- journal
class Journal:
    """What the Journal tab needs. `VaultJournal` is the real one; tests and the demo use a plain in-memory one."""

    def entries(self) -> list[dict]: ...
    def delete(self, entry_id: str) -> bool: ...
    def clear(self) -> int: ...


class VaultJournal(Journal):
    """Her private notebook: the notes she said yes to keeping (check-in answers and exercise recaps), read from the reflection vault."""

    def __init__(self, vault):
        self.vault = vault

    def entries(self) -> list[dict]:
        try:
            rows = self.vault.list("reflection")
        except Exception:
            return []
        return [{"id": r["id"], "created_at": r["created_at"], "text": r["value"], "label": (r.get("key") or "").replace("_", " ")}
                for r in sorted(rows, key=lambda r: -r["created_at"])]

    def delete(self, entry_id: str) -> bool:
        try:
            self.vault.delete(entry_id)
            return True
        except Exception:
            return False

    def clear(self) -> int:
        try:
            return self.vault.clear("reflection")
        except Exception:
            return 0


# ------------------------------------------------------------------------------------------------- auth
class AuthStore:
    def __init__(self, path: str | Path, clock=time.time):
        self.path, self.clock = Path(path), clock
        self._lock = threading.Lock()
        self._sessions: dict[str, float] = {}          # sha256(token) -> last seen
        self._fails: dict[str, list[float]] = {}
        self.d = {"passphrase": None, "pairing": None}
        try:
            self.d = json.loads(self.path.read_text())
        except (OSError, ValueError):
            pass

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(self.d, f)
        os.replace(tmp, self.path)

    @staticmethod
    def _hash(secret: str, salt: bytes) -> str:
        return hashlib.scrypt(secret.encode(), salt=salt, **SCRYPT).hex()

    @property
    def configured(self) -> bool:
        return bool(self.d.get("passphrase"))

    def create_pairing_code(self) -> str:
        """Printed on the host by the owner; lets HER set a passphrase the owner never sees. One use, one hour."""
        raw = "".join(secrets.choice("ABCDEFGHJKLMNPQRSTUVWXYZ23456789") for _ in range(8))
        salt = os.urandom(16)
        with self._lock:
            self.d["pairing"] = {"salt": salt.hex(), "hash": self._hash(raw, salt), "expires": self.clock() + PAIRING_TTL_S}
            self._save()
        return raw[:4] + "-" + raw[4:]

    def setup(self, code: str, passphrase: str) -> bool:
        code = re.sub(r"[^A-Za-z0-9]", "", code or "").upper()
        with self._lock:
            p = self.d.get("pairing")
            if self.configured or not p or self.clock() > p["expires"] or len(passphrase or "") < MIN_PASSPHRASE:
                return False
            if not hmac.compare_digest(self._hash(code, bytes.fromhex(p["salt"])), p["hash"]):
                return False
            salt = os.urandom(16)
            self.d = {"passphrase": {"salt": salt.hex(), "hash": self._hash(passphrase, salt)}, "pairing": None}
            self._save()
            return True

    def locked_out(self, who: str) -> bool:
        now = self.clock()
        self._fails[who] = [t for t in self._fails.get(who, []) if now - t < LOGIN_WINDOW_S]
        return len(self._fails[who]) >= LOGIN_FAILS

    def login(self, who: str, passphrase: str) -> str | None:
        with self._lock:
            if self.locked_out(who) or not self.configured:
                return None
            p = self.d["passphrase"]
            ok = hmac.compare_digest(self._hash(passphrase or "", bytes.fromhex(p["salt"])), p["hash"])
            if not ok:
                self._fails.setdefault(who, []).append(self.clock())
                return None
            self._fails.pop(who, None)
            token = secrets.token_urlsafe(32)
            self._sessions[hashlib.sha256(token.encode()).hexdigest()] = self.clock()
            return token

    def valid(self, token: str | None) -> bool:
        if not token:
            return False
        h = hashlib.sha256(token.encode()).hexdigest()
        with self._lock:
            seen = self._sessions.get(h)
            if seen is None or self.clock() - seen > SESSION_IDLE_S:
                self._sessions.pop(h, None)
                return False
            self._sessions[h] = self.clock()
            return True

    def logout(self, token: str | None) -> None:
        if token:
            with self._lock:
                self._sessions.pop(hashlib.sha256(token.encode()).hexdigest(), None)

    def change_passphrase(self, old: str, new: str) -> bool:
        with self._lock:
            if not self.configured or len(new or "") < MIN_PASSPHRASE:
                return False
            p = self.d["passphrase"]
            if not hmac.compare_digest(self._hash(old or "", bytes.fromhex(p["salt"])), p["hash"]):
                return False
            salt = os.urandom(16)
            self.d["passphrase"] = {"salt": salt.hex(), "hash": self._hash(new, salt)}
            self._sessions.clear()
            self._save()
            return True


# ------------------------------------------------------------------------------------------------- server
class WebApp:
    def __init__(self, store: ProductivityStore, journal: Journal, auth: AuthStore | None, clock=time.time, host: str = "127.0.0.1",
                 port: int = 8790, tls: tuple[str, str] | None = None, allowed_hosts: list[str] | None = None,
                 name=lambda: None, demo: bool = False, extra: dict | None = None):
        if host not in ("127.0.0.1", "localhost", "::1") and not tls:
            raise WebAppError("listening beyond this computer needs a TLS certificate (tls=(cert, key)); otherwise use 127.0.0.1")
        if demo and host not in ("127.0.0.1", "localhost"):
            raise WebAppError("demo mode (no login, sample data) is only allowed on localhost")
        if not demo and auth is None:
            raise WebAppError("a login store is required")
        self.store, self.journal, self.auth, self.clock = store, journal, auth, clock
        self.host, self.port, self.tls, self.name, self.demo = host, port, tls, name, demo
        self.extra_hosts = set(allowed_hosts or [])
        self.extra = extra or {}
        self.url = ""
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    # ---- lifecycle ----------------------------------------------------------------------------------
    def start(self) -> str:
        self._httpd = ThreadingHTTPServer((self.host, self.port), self._handler())
        self._httpd.daemon_threads = True
        if self.tls:
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ctx.minimum_version = ssl.TLSVersion.TLSv1_2
            ctx.load_cert_chain(*self.tls)
            self._httpd.socket = ctx.wrap_socket(self._httpd.socket, server_side=True)
        self.port = self._httpd.server_address[1]
        self.url = f"{'https' if self.tls else 'http'}://localhost:{self.port}"
        self._thread = threading.Thread(target=self._httpd.serve_forever, name="phone-app", daemon=True)
        self._thread.start()
        return self.url

    def stop(self) -> None:
        if self._httpd:
            self._httpd.shutdown()
            self._httpd.server_close()
        if self._thread:
            self._thread.join(5)

    # ---- request handling ---------------------------------------------------------------------------
    def _handler(self):
        app = self

        class H(BaseHTTPRequestHandler):
            server_version = "NineTailsApp"
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):
                pass

            def _hosts(self) -> set[str]:
                p = app.port
                return {f"localhost:{p}", f"127.0.0.1:{p}", *{f"{h}:{p}" for h in app.extra_hosts}}

            def _send(self, status: int, body: bytes, ctype: str = "application/json", extra: dict | None = None) -> None:
                self.send_response(status)
                self.send_header("Content-Type", ctype + ("; charset=utf-8" if ctype.startswith("text/") or ctype.endswith("json") else ""))
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Referrer-Policy", "no-referrer")
                self.send_header("X-Frame-Options", "DENY")
                self.send_header("Content-Security-Policy", "default-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
                for k, v in (extra or {}).items():
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(body)

            def _json(self, status: int, obj, extra: dict | None = None) -> None:
                self._send(status, json.dumps(obj).encode(), extra=extra)

            def _token(self) -> str | None:
                for part in (self.headers.get("Cookie") or "").split(";"):
                    k, _, v = part.strip().partition("=")
                    if k == "nt_session":
                        return v
                return None

            def _authed(self) -> bool:
                return app.demo or bool(app.auth and app.auth.valid(self._token()))

            def _guard(self, write: bool) -> bool:
                if self.headers.get("Host") not in self._hosts():
                    self._json(403, {"error": "bad host"})
                    return False
                if write:
                    origin = self.headers.get("Origin")
                    if self.headers.get("X-NT-App") != "1" or (origin is not None and origin not in {f"http{'s' if app.tls else ''}://{h}" for h in self._hosts()}):
                        self._json(403, {"error": "forbidden"})
                        return False
                return True

            def _body(self):
                try:
                    n = int(self.headers.get("Content-Length") or 0)       # browsers send no length for a DELETE without a body
                except ValueError:
                    n = -1
                if not 0 <= n <= MAX_BODY:
                    self._json(413 if n > MAX_BODY else 400, {"error": "bad length"})
                    return None
                try:
                    d = json.loads(self.rfile.read(n) or b"{}")
                except ValueError:
                    self._json(400, {"error": "bad json"})
                    return None
                if not isinstance(d, dict):
                    self._json(400, {"error": "bad json"})
                    return None
                return d

            def do_GET(self):
                if not self._guard(False):
                    return
                u = urlsplit(self.path)
                if u.path in STATIC_FILES:
                    f = STATIC / STATIC_FILES[u.path]
                    ctype = mimetypes.guess_type(f.name)[0] or "application/octet-stream"
                    if f.suffix == ".webmanifest":
                        ctype = "application/manifest+json"
                    return self._send(200, f.read_bytes(), ctype)
                if u.path == "/favicon.ico":
                    return self._send(204, b"", "image/x-icon")
                if not u.path.startswith("/api/"):
                    return self._json(404, {"error": "not found"})
                if u.path == "/api/session":
                    return self._json(200, {"authenticated": self._authed(), "setup_needed": bool(app.auth and not app.auth.configured and not app.demo),
                                            "demo": app.demo, "name": app.name() if self._authed() else None, "encrypted": app.store.encrypted,
                                            "capabilities": app.extra.get("capabilities", {}) if self._authed() else {}})
                if not self._authed():
                    return self._json(401, {"error": "login required"})
                q = {k: v[0] for k, v in parse_qs(u.query).items()}
                return app._get(self, u.path, q)

            def do_POST(self):
                self._write("POST")

            def do_PUT(self):
                self._write("PUT")

            def do_DELETE(self):
                self._write("DELETE")

            def _write(self, method: str):
                if not self._guard(True):
                    return
                path = urlsplit(self.path).path
                body = self._body()
                if body is None:
                    return
                if path == "/api/setup" and method == "POST":
                    if app.demo or app.auth is None:
                        return self._json(404, {"error": "not found"})
                    if app.auth.setup(str(body.get("code", "")), str(body.get("passphrase", ""))):
                        return self._json(200, {"ok": True})
                    return self._json(400, {"error": "That code or passphrase didn't work. The code lasts one hour; the passphrase needs 8 or more characters."})
                if path == "/api/login" and method == "POST":
                    if app.demo or app.auth is None:
                        return self._json(200, {"ok": True})
                    who = self.client_address[0]
                    if app.auth.locked_out(who):
                        return self._json(429, {"error": "Too many tries. Please wait a few minutes."})
                    tok = app.auth.login(who, str(body.get("passphrase", "")))
                    if not tok:
                        return self._json(401, {"error": "That passphrase isn't right."})
                    flags = "HttpOnly; SameSite=Strict; Path=/; Max-Age=%d" % int(SESSION_IDLE_S) + ("; Secure" if app.tls else "")
                    return self._json(200, {"ok": True}, extra={"Set-Cookie": f"nt_session={tok}; {flags}"})
                if not self._authed():
                    return self._json(401, {"error": "login required"})
                if path == "/api/logout" and method == "POST":
                    if app.auth:
                        app.auth.logout(self._token())
                    return self._json(200, {"ok": True}, extra={"Set-Cookie": "nt_session=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0"})
                return app._write(self, method, path, body)

        return H

    # ---- API ----------------------------------------------------------------------------------------
    @staticmethod
    def _note_out(n: dict) -> dict:
        return n

    def _get(self, h, path: str, q: dict) -> None:
        if path == "/api/notes":
            try:
                rows = self.store.notes(q.get("q", "")[:200], kind=q.get("kind") or None, archived=q.get("archived") == "1", limit=300)
            except Exception as e:
                return h._json(500, {"error": f"{type(e).__name__}"})
            return h._json(200, {"notes": rows})
        m = re.fullmatch(r"/api/notes/([0-9a-f]{12})", path)
        if m:
            n = self.store.get_note(m.group(1))
            return h._json(200 if n else 404, n or {"error": "no such note"})
        if path == "/api/lists":
            return h._json(200, {"lists": self.store.lists(archived=q.get("archived") == "1")})
        m = re.fullmatch(r"/api/lists/([0-9a-f]{12})", path)
        if m:
            l = self.store.get_list(m.group(1))
            return h._json(200 if l else 404, l or {"error": "no such list"})
        if path == "/api/journal":
            return h._json(200, {"entries": self.journal.entries()})
        if path == "/api/settings":
            return h._json(200, {"dictation_cleanup": self.store.setting("dictation_cleanup", "off") == "on"})
        if path == "/api/export":
            data = self.store.export()
            data["journal"] = self.journal.entries()
            body = json.dumps(data, indent=1).encode()
            return h._send(200, body, "application/json", {"Content-Disposition": 'attachment; filename="nine-tails-export.json"'})
        return h._json(404, {"error": "not found"})

    def _write(self, h, method: str, path: str, b: dict) -> None:
        try:
            return self._write_inner(h, method, path, b)
        except StoreError as e:
            return h._json(400, {"error": str(e)[:200]})
        except (KeyError, ValueError, TypeError):
            return h._json(400, {"error": "bad request"})

    def _write_inner(self, h, method: str, path: str, b: dict) -> None:
        S = self.store
        if path == "/api/notes" and method == "POST":
            n = S.add_note(str(b.get("body", "")), kind="written", title=b.get("title"), tags=b.get("tags"), source="phone")
            return h._json(201, n)
        m = re.fullmatch(r"/api/notes/([0-9a-f]{12})(/append)?", path)
        if m:
            nid, app_ = m.group(1), m.group(2)
            if method == "PUT" and not app_:
                n = S.update_note(nid, title=b.get("title"), body=b.get("body"), tags=b.get("tags"), archived=b.get("archived"))
                return h._json(200, n)
            if method == "POST" and app_:
                return h._json(200, S.update_note(nid, append=str(b.get("text", ""))))
            if method == "DELETE" and not app_:
                return h._json(200 if S.delete_note(nid) else 404, {"ok": True})
        if path == "/api/lists" and method == "POST":
            return h._json(201, S.create_list(str(b.get("name", "")), [str(x) for x in b.get("items", [])][:50]))
        m = re.fullmatch(r"/api/lists/([0-9a-f]{12})(/items|/move)?", path)
        if m:
            lid, sub = m.group(1), m.group(2)
            if method == "PUT" and not sub:
                if "name" in b:
                    S.rename_list(lid, str(b["name"]))
                return h._json(200, S.get_list(lid))
            if method == "DELETE" and not sub:
                return h._json(200 if S.delete_list(lid) else 404, {"ok": True})
            if method == "POST" and sub == "/items":
                due = b.get("due_at")
                S.add_item(lid, str(b.get("text", "")), float(due) if isinstance(due, (int, float)) and not isinstance(due, bool) else None)
                return h._json(201, S.get_list(lid))
            if method == "POST" and sub == "/move":
                S.move_list(lid, -1 if b.get("direction") == "up" else 1)
                return h._json(200, {"lists": S.lists()})
        m = re.fullmatch(r"/api/items/([0-9a-f]{12})(/move)?", path)
        if m:
            iid, mv = m.group(1), m.group(2)
            it = S.get_item(iid)
            if it is None:
                return h._json(404, {"error": "no such item"})
            if method == "PUT" and not mv:
                due = b.get("due_at", "keep")
                if due != "keep" and due is not None and (isinstance(due, bool) or not isinstance(due, (int, float))):
                    raise ValueError
                S.update_item(iid, text=b.get("text"), done=b.get("done") if isinstance(b.get("done"), bool) else None, due_at=due)
                return h._json(200, S.get_list(it["list_id"]))
            if method == "DELETE" and not mv:
                S.delete_item(iid)
                return h._json(200, S.get_list(it["list_id"]))
            if method == "POST" and mv:
                S.move_item(iid, -1 if b.get("direction") == "up" else 1)
                return h._json(200, S.get_list(it["list_id"]))
        m = re.fullmatch(r"/api/journal/([\w\-]{4,64})", path)
        if m and method == "DELETE":
            return h._json(200 if self.journal.delete(m.group(1)) else 404, {"ok": True})
        if path == "/api/settings" and method == "PUT":
            if isinstance(b.get("dictation_cleanup"), bool):
                S.set_setting("dictation_cleanup", "on" if b["dictation_cleanup"] else "off")
            return h._json(200, {"dictation_cleanup": S.setting("dictation_cleanup", "off") == "on"})
        if path == "/api/delete-all" and method == "POST":
            if b.get("confirm") != "DELETE":
                return h._json(400, {"error": "type DELETE to confirm"})
            gone = S.delete_everything()
            gone["journal"] = self.journal.clear()
            return h._json(200, gone)
        if path == "/api/passphrase" and method == "POST" and self.auth:
            ok = self.auth.change_passphrase(str(b.get("old", "")), str(b.get("new", "")))
            return h._json(200 if ok else 400, {"ok": ok} if ok else {"error": "That didn't work. The new passphrase needs 8 or more characters."})
        return h._json(404, {"error": "not found"})
