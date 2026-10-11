import io
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from tools import check_llm


class Handler(BaseHTTPRequestHandler):
    models = ["llama3.2:3b"]

    def log_message(self, *a):
        pass

    def _send(self, obj):
        body = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self._send({"models": [{"name": n} for n in self.models]})

    def do_POST(self):
        self.rfile.read(int(self.headers["Content-Length"]))
        self._send({"message": {"content": "Hello there!"}})


@pytest.fixture
def server():
    s = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{s.server_port}"
    s.shutdown()


def run(*args):
    out = io.StringIO()
    return check_llm.main(list(args), out=out), out.getvalue()


def test_reports_models_and_answer(server):
    rc, text = run("--url", server, "--model", "llama3.2:3b")
    assert rc == 0 and "llama3.2:3b" in text and "Hello there!" in text


def test_missing_model_tells_you_to_pull_it_on_the_server(server, capsys):
    rc, _ = run("--url", server, "--model", "nope:1b")
    assert rc == 1 and "ON THE SERVER" in capsys.readouterr().err


def test_unreachable_server_gives_a_useful_hint(capsys):
    rc, _ = run("--url", "http://127.0.0.1:9", "--model", "x", "--timeout", "2")
    assert rc == 1 and "OLLAMA_HOST" in capsys.readouterr().err


def test_public_address_is_refused(capsys):
    rc, _ = run("--url", "http://8.8.8.8:11434", "--model", "x")
    assert rc == 1 and "not a local" in capsys.readouterr().err
