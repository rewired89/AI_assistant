import json
import shutil
import subprocess
import urllib.error
import urllib.request

import pytest

from companion.productivity import ProductivityStore
from companion.webapp import AuthStore, Journal, WebApp, WebAppError
from companion.webapp.server import LOGIN_FAILS, SESSION_IDLE_S, STATIC


class Clock:
    def __init__(self):
        self.t = 1_800_000_000.0

    def __call__(self):
        return self.t


class MemJournal(Journal):
    def __init__(self):
        self.rows = [{"id": "j1abc", "created_at": 1.0, "text": "Today: a good walk.", "label": "checkin"}]

    def entries(self):
        return list(self.rows)

    def delete(self, i):
        n = len(self.rows)
        self.rows = [r for r in self.rows if r["id"] != i]
        return n != len(self.rows)

    def clear(self):
        n, self.rows = len(self.rows), []
        return n


class Client:
    def __init__(self, app, host=None):
        self.app, self.cookie = app, None
        self.host = host or f"localhost:{app.port}"

    def call(self, method, path, body=None, headers=None, write_header=True, host=None):
        h = {"Host": host or self.host}
        if self.cookie:
            h["Cookie"] = self.cookie
        if write_header and method != "GET":
            h["X-NT-App"] = "1"
        h.update(headers or {})
        data = json.dumps(body).encode() if body is not None else (b"{}" if method != "GET" else None)
        req = urllib.request.Request(self.app.url + path, data=data, method=method, headers=h)
        try:
            with urllib.request.urlopen(req) as r:
                if r.headers.get("Set-Cookie"):
                    self.cookie = r.headers["Set-Cookie"].split(";")[0]
                raw = r.read()
                return r.status, (json.loads(raw) if r.headers.get("Content-Type", "").startswith("application/json") else raw), r.headers
        except urllib.error.HTTPError as e:
            raw = e.read()
            try:
                return e.code, json.loads(raw), e.headers
            except ValueError:
                return e.code, raw, e.headers


@pytest.fixture
def env(tmp_path):
    clock = Clock()
    store = ProductivityStore(tmp_path / "p.db", clock)
    auth = AuthStore(tmp_path / "auth.json", clock)
    journal = MemJournal()
    app = WebApp(store, journal, auth, clock, port=0, name=lambda: "Sam")
    app.start()
    yield app, store, auth, journal, clock
    app.stop()
    store.close()


def login(app, auth):
    code = auth.create_pairing_code()
    c = Client(app)
    assert c.call("POST", "/api/setup", {"code": code, "passphrase": "my own passphrase"})[0] == 200
    assert c.call("POST", "/api/login", {"passphrase": "my own passphrase"})[0] == 200
    return c


def test_nothing_is_readable_without_logging_in(env):
    app, _, auth, *_ = env
    c = Client(app)
    for p in ("/api/notes", "/api/lists", "/api/journal", "/api/export", "/api/settings"):
        assert c.call("GET", p)[0] == 401
    st, body, _ = c.call("GET", "/api/session")
    assert st == 200 and body["authenticated"] is False and body["setup_needed"] is True and body["name"] is None
    assert c.call("POST", "/api/notes", {"body": "x"})[0] == 401


def test_setup_needs_the_pairing_code_and_works_once(env):
    app, _, auth, *_ = env
    c = Client(app)
    assert c.call("POST", "/api/setup", {"code": "AAAA-BBBB", "passphrase": "my own passphrase"})[0] == 400      # no code issued
    code = auth.create_pairing_code()
    assert c.call("POST", "/api/setup", {"code": "WRONG-CODE", "passphrase": "my own passphrase"})[0] == 400
    assert c.call("POST", "/api/setup", {"code": code, "passphrase": "short"})[0] == 400
    assert c.call("POST", "/api/setup", {"code": code.lower(), "passphrase": "my own passphrase"})[0] == 200
    assert c.call("POST", "/api/setup", {"code": code, "passphrase": "another passphrase"})[0] == 400            # used up
    assert "my own passphrase" not in (app.auth.path.read_text())                                                  # never stored plain


def test_pairing_code_expires(env):
    app, _, auth, _, clock = env
    code = auth.create_pairing_code()
    clock.t += 3601
    assert Client(app).call("POST", "/api/setup", {"code": code, "passphrase": "my own passphrase"})[0] == 400


def test_login_is_rate_limited_and_wrong_passphrases_fail(env):
    app, _, auth, *_ = env
    login(app, auth)
    c = Client(app)
    for _ in range(LOGIN_FAILS):
        assert c.call("POST", "/api/login", {"passphrase": "wrong wrong wrong"})[0] == 401
    assert c.call("POST", "/api/login", {"passphrase": "my own passphrase"})[0] == 429                            # locked even with the right one


def test_session_cookie_flags_expiry_and_logout(env):
    app, _, auth, _, clock = env
    code = auth.create_pairing_code()
    c = Client(app)
    c.call("POST", "/api/setup", {"code": code, "passphrase": "my own passphrase"})
    st, _, headers = c.call("POST", "/api/login", {"passphrase": "my own passphrase"})
    cookie = headers["Set-Cookie"]
    assert "HttpOnly" in cookie and "SameSite=Strict" in cookie and "Secure" not in cookie                          # plain localhost: not TLS
    assert c.call("GET", "/api/notes")[0] == 200
    clock.t += SESSION_IDLE_S + 1
    assert c.call("GET", "/api/notes")[0] == 401                                                                    # idle too long
    c.call("POST", "/api/login", {"passphrase": "my own passphrase"})
    assert c.call("GET", "/api/notes")[0] == 200
    c.call("POST", "/api/logout")
    assert c.call("GET", "/api/notes")[0] == 401


def test_writes_need_the_header_and_a_same_origin_and_the_host_must_be_ours(env):
    app, _, auth, *_ = env
    c = login(app, auth)
    assert c.call("POST", "/api/notes", {"body": "x"}, write_header=False)[0] == 403
    assert c.call("POST", "/api/notes", {"body": "x"}, headers={"Origin": "http://evil.example"})[0] == 403
    assert c.call("GET", "/api/notes", host="evil.example")[0] == 403                                               # DNS rebinding
    assert c.call("POST", "/api/notes", {"body": "x"}, headers={"Origin": f"http://localhost:{app.port}"})[0] == 201


def test_notes_crud_search_append_archive_delete(env):
    app, store, auth, *_ = env
    c = login(app, auth)
    n = c.call("POST", "/api/notes", {"title": "Plan", "body": "robot enclosure with magnets", "tags": ["Robot"]})[1]
    assert n["kind"] == "written" and n["tags"] == ["robot"]
    assert c.call("GET", "/api/notes?q=magnets")[1]["notes"][0]["id"] == n["id"]
    assert c.call("POST", f"/api/notes/{n['id']}/append", {"text": "and two colours"})[1]["body"].endswith("and two colours")
    assert c.call("PUT", f"/api/notes/{n['id']}", {"title": "Renamed"})[1]["title"] == "Renamed"
    assert c.call("PUT", f"/api/notes/{n['id']}", {"archived": True})[0] == 200
    assert c.call("GET", "/api/notes")[1]["notes"] == [] and len(c.call("GET", "/api/notes?archived=1")[1]["notes"]) == 1
    assert c.call("DELETE", f"/api/notes/{n['id']}")[0] == 200 and c.call("GET", f"/api/notes/{n['id']}")[0] == 404
    assert c.call("POST", "/api/notes", {"body": "   "})[0] == 400                                                  # nothing half-saved
    assert c.call("GET", "/api/notes/..%2f..%2fetc")[0] in (404,)


def test_a_note_dictated_to_the_robot_appears_on_the_phone_and_edits_flow_back(env):
    from companion.productivity.talk import NotesTalk
    app, store, auth, _, clock = env
    c = login(app, auth)
    talk = NotesTalk(store, clock, phone_hint=True)
    assert "saved your" in talk.handle("write this down: I need to research sprite memory use").text
    n = c.call("GET", "/api/notes")[1]["notes"][0]
    assert n["kind"] == "dictated" and n["source"] == "voice" and n["body"] == "I need to research sprite memory use"
    c.call("PUT", f"/api/notes/{n['id']}", {"body": "I need to research sprite memory use, and the palette size"})
    assert "palette size" in talk.handle("read my last note").text                                                   # the robot reads the phone's edit


def test_lists_by_voice_then_checked_off_on_the_phone_and_back(env):
    from companion.productivity.talk import NotesTalk
    app, store, auth, _, clock = env
    c = login(app, auth)
    talk = NotesTalk(store, clock)
    assert "added 4 items" in talk.handle("Make me a grocery list. We need eggs, rice, milk and fruit").text
    lst = c.call("GET", "/api/lists")[1]["lists"][0]
    items = c.call("GET", f"/api/lists/{lst['id']}")[1]["items"]
    assert [i["text"] for i in items] == ["eggs", "rice", "milk", "fruit"]
    r = c.call("PUT", f"/api/items/{items[0]['id']}", {"done": True})[1]
    assert r["done"] == 1
    assert "eggs" not in talk.handle("what's on my grocery list").text
    c.call("PUT", f"/api/items/{items[0]['id']}", {"done": False})                                                    # restored
    assert "eggs" in talk.handle("what's on my grocery list").text
    c.call("POST", f"/api/items/{items[3]['id']}/move", {"direction": "up"})
    assert [i["text"] for i in c.call("GET", f"/api/lists/{lst['id']}")[1]["items"]] == ["eggs", "rice", "fruit", "milk"]
    assert c.call("POST", f"/api/lists/{lst['id']}/items", {"text": "bananas"})[0] == 201
    assert c.call("PUT", f"/api/lists/{lst['id']}", {"name": "Groceries"})[1]["name"] == "Groceries"
    assert c.call("PUT", f"/api/items/{items[0]['id']}", {"text": ""})[0] == 400
    assert c.call("DELETE", f"/api/lists/{lst['id']}")[0] == 200 and c.call("GET", f"/api/lists/{lst['id']}")[0] == 404


def test_journal_is_readable_and_deletable_by_her(env):
    app, _, auth, journal, _ = env
    c = login(app, auth)
    e = c.call("GET", "/api/journal")[1]["entries"]
    assert e[0]["text"] == "Today: a good walk."
    assert c.call("DELETE", "/api/journal/j1abc")[0] == 200 and c.call("GET", "/api/journal")[1]["entries"] == []
    assert c.call("DELETE", "/api/journal/nope1")[0] == 404


def test_the_real_vault_journal_shows_only_notes_she_saved(tmp_path):
    from companion.memory import ReflectionVault
    from companion.webapp import VaultJournal
    clock = Clock()
    vault = ReflectionVault(tmp_path / "v.db", "her", clock)
    vault.set_enabled("reflection", True)
    vault.add({"category": "reflection", "key": "checkin_2026-10-12", "value": "Today: I finished the painting", "tags": [], "source": "explicit_user_statement"}, consent=True)
    j = VaultJournal(vault)
    rows = j.entries()
    assert len(rows) == 1 and rows[0]["text"].startswith("Today: I finished") and "checkin" in rows[0]["label"]
    assert j.delete(rows[0]["id"]) and j.entries() == []


def test_export_and_delete_everything(env):
    app, store, auth, journal, _ = env
    c = login(app, auth)
    c.call("POST", "/api/notes", {"body": "keep this", "title": "T"})
    c.call("POST", "/api/lists", {"name": "L", "items": ["a", "b"]})
    st, raw, headers = c.call("GET", "/api/export")
    data = json.loads(raw) if isinstance(raw, bytes) else raw
    assert st == 200 and "attachment" in headers["Content-Disposition"]
    assert len(data["notes"]) == 1 and data["lists"][0]["items"][0]["text"] == "a" and data["journal"][0]["text"].startswith("Today")
    assert c.call("POST", "/api/delete-all", {"confirm": "nope"})[0] == 400 and len(store.notes()) == 1
    st, gone, _ = c.call("POST", "/api/delete-all", {"confirm": "DELETE"})
    assert gone == {"notes": 1, "lists": 1, "journal": 1} and store.notes() == [] and store.lists() == [] and journal.entries() == []


def test_settings_toggle_reaches_the_robots_dictation(env):
    from companion.productivity.talk import NotesTalk
    app, store, auth, _, clock = env
    c = login(app, auth)
    assert c.call("GET", "/api/settings")[1]["dictation_cleanup"] is False
    c.call("PUT", "/api/settings", {"dictation_cleanup": True})
    NotesTalk(store, clock).handle("write this down: um buy milk")
    assert store.notes()[0]["body"] == "Buy milk." and store.notes()[0]["cleaned"] is True


def test_change_passphrase_ends_old_sessions(env):
    app, _, auth, *_ = env
    c = login(app, auth)
    assert c.call("POST", "/api/passphrase", {"old": "wrong", "new": "a brand new passphrase"})[0] == 400
    assert c.call("POST", "/api/passphrase", {"old": "my own passphrase", "new": "a brand new passphrase"})[0] == 200
    assert c.call("GET", "/api/notes")[0] == 401
    assert c.call("POST", "/api/login", {"passphrase": "a brand new passphrase"})[0] == 200


def test_oversized_and_malformed_bodies_are_refused(env):
    app, _, auth, *_ = env
    c = login(app, auth)
    assert c.call("POST", "/api/notes", {"body": "x" * 300_000})[0] == 413
    req = urllib.request.Request(app.url + "/api/notes", data=b"[1,2]", method="POST", headers={"Host": c.host, "X-NT-App": "1", "Cookie": c.cookie})
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(req)
    assert e.value.code == 400


def test_static_files_and_security_headers(env):
    app, *_ = env
    c = Client(app)
    for p, kind in (("/", b"<title>Nine Tails</title>"), ("/app.js", b"Nine Tails phone app"), ("/manifest.webmanifest", b"Nine Tails")):
        st, body, headers = c.call("GET", p)
        assert st == 200 and kind in body
        assert "default-src 'self'" in headers["Content-Security-Policy"] and headers["X-Content-Type-Options"] == "nosniff" and headers["Cache-Control"] == "no-store"
    assert c.call("GET", "/../companion/webapp/server.py")[0] == 404
    assert c.call("GET", "/app.js%2f..")[0] == 404


def test_listening_beyond_this_computer_needs_tls_and_demo_is_localhost_only(tmp_path):
    clock = Clock()
    store = ProductivityStore(tmp_path / "p.db", clock)
    auth = AuthStore(tmp_path / "a.json", clock)
    with pytest.raises(WebAppError):
        WebApp(store, MemJournal(), auth, clock, host="0.0.0.0")
    with pytest.raises(WebAppError):
        WebApp(store, MemJournal(), None, clock, host="0.0.0.0", demo=True)
    with pytest.raises(WebAppError):
        WebApp(store, MemJournal(), None, clock, port=0)                                                              # no auth, not demo


def test_demo_mode_needs_no_login_and_says_so(tmp_path):
    clock = Clock()
    store = ProductivityStore(tmp_path / "p.db", clock)
    app = WebApp(store, MemJournal(), None, clock, port=0, demo=True, name=lambda: "Sam")
    app.start()
    try:
        c = Client(app)
        st, body, _ = c.call("GET", "/api/session")
        assert body["demo"] is True and body["authenticated"] is True and body["name"] == "Sam"
        assert c.call("GET", "/api/notes")[0] == 200
    finally:
        app.stop()


def test_served_over_tls_marks_the_cookie_secure(tmp_path):
    import ssl
    from tools.gen_certs import generate
    pki = generate(tmp_path / "tls", ["localhost", "127.0.0.1"])
    clock = Clock()
    store = ProductivityStore(tmp_path / "p.db", clock)
    auth = AuthStore(tmp_path / "a.json", clock)
    app = WebApp(store, MemJournal(), auth, clock, port=0, tls=(str(pki["server_cert"]), str(pki["server_key"])))
    app.start()
    try:
        code = auth.create_pairing_code()
        ctx = ssl.create_default_context(cafile=str(pki["ca_cert"]))

        def post(path, body):
            req = urllib.request.Request(app.url + path, data=json.dumps(body).encode(), method="POST",
                                         headers={"Host": f"localhost:{app.port}", "X-NT-App": "1"})
            return urllib.request.urlopen(req, context=ctx)
        assert post("/api/setup", {"code": code, "passphrase": "my own passphrase"}).status == 200
        assert "Secure" in post("/api/login", {"passphrase": "my own passphrase"}).headers["Set-Cookie"]
    finally:
        app.stop()


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_phone_app_script_is_valid_javascript():
    assert subprocess.run(["node", "--check", str(STATIC / "app.js")], capture_output=True).returncode == 0
    assert subprocess.run(["node", "--check", str(STATIC / "sw.js")], capture_output=True).returncode == 0
