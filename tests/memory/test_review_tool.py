import io

import pytest
import yaml

from companion.memory import MemoryStore, ReflectionVault
from tools import memory_review as mr

PW = "a long enough passphrase"


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    (tmp_path / "data").mkdir()
    c = {"species": "ninetales", "device_id": "robot-01", "secrets": "k.json", "data_dir": "data",
         "tls": {"cert": "c", "key": "k"}, "memory": {"encrypt": True, "passphrase_env": "NT_REVIEW_PW"}}
    (tmp_path / "c.yaml").write_text(yaml.safe_dump(c))
    monkeypatch.setenv("NT_REVIEW_PW", PW)
    m = MemoryStore(tmp_path / "data" / "memory.db", "her", lambda: 1.0, PW)
    a = m.add({"category": "profile", "key": "pet", "value": "a cat"})
    b = m.add({"category": "semantic", "value": "likes rain", "source": "ai_inference"})
    m.close()
    v = ReflectionVault(tmp_path / "data" / "reflections.db", "her", lambda: 1.0, PW)
    v.set_enabled("reflection", True)
    v.add({"category": "reflection", "value": "SECRET WORRY"}, consent=True)
    v.close()
    return tmp_path / "c.yaml", a, b


def run(cfg, *args):
    out = io.StringIO()
    rc = mr.main(["--config", str(cfg), *args], out=out)
    return rc, out.getvalue()


def test_list_confirm_delete_clear_export(cfg):
    path, a, b = cfg
    rc, text = run(path, "list")
    assert rc == 0 and "pet: a cat" in text and "likes rain" in text
    assert "likes rain" in run(path, "list", "--pending")[1] and "a cat" not in run(path, "list", "--pending")[1]
    assert run(path, "confirm", b)[0] == 0 and run(path, "list", "--pending")[1].strip() == ""
    assert run(path, "delete", a)[0] == 0 and "a cat" not in run(path, "list")[1]
    assert run(path, "clear", "semantic")[0] == 2 and "likes rain" in run(path, "list")[1]     # needs --yes
    assert "deleted 1" in run(path, "clear", "semantic", "--yes")[1]
    assert '"memories"' in run(path, "export")[1]


def test_vault_contents_are_never_shown_and_can_be_deleted(cfg):
    path, *_ = cfg
    rc, text = run(path, "vault-status")
    assert rc == 0 and "1 private" in text and "SECRET" not in text
    for cmd in ("list", "export"):
        assert "SECRET" not in run(path, cmd)[1]
    assert run(path, "vault-clear")[0] == 2
    assert "deleted 1" in run(path, "vault-clear", "--yes")[1]
    assert "0 private" in run(path, "vault-status")[1]


def test_missing_passphrase_and_bad_ids_are_errors(cfg, monkeypatch):
    path, *_ = cfg
    assert run(path, "delete", "nope")[0] == 1
    monkeypatch.delenv("NT_REVIEW_PW")
    assert run(path, "list")[0] == 1
