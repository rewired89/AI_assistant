import sqlite3

import pytest

from companion.memory import (CryptoError, EncryptionError, MemoryError_, MemoryStore, ReflectionVault, WrongUser,
                              encrypt_existing)
from tools import memory_crypt

PW = "correct horse battery"
SECRETS = ["likes jasmine tea", "dentist anxiety", "sister Maria", "private worry about exams"]


@pytest.fixture
def clock():
    class C:
        t = 1_800_000_000.0
        def __call__(self): return self.t
    return C()


def raw_bytes(path):
    return path.read_bytes()


def fill(m):
    m.add({"category": "profile", "key": "favorite_drink", "value": SECRETS[0], "tags": ["tea"], "source_context": "she said so"})
    m.add({"category": "episodic", "value": SECRETS[1]})
    m.add({"category": "semantic", "value": SECRETS[2], "source": "ai_inference"})


def test_nothing_readable_on_disk_and_everything_works_after_reopen(tmp_path, clock):
    p = tmp_path / "m.db"
    m = MemoryStore(p, "her", clock, PW)
    fill(m)
    m.delete(m.list("episodic")[0]["id"])                 # deleted rows must not linger either
    m.close()
    data = raw_bytes(p)
    for s in SECRETS + ["favorite_drink", "jasmine", "tea", "she said so"]:
        assert s.encode() not in data, s
    m = MemoryStore(p, "her", clock, PW)
    assert [x["value"] for x in m.search("jasmine tea")] == [SECRETS[0]]
    assert m.list("profile")[0]["key"] == "favorite_drink" and m.list("profile")[0]["tags"] == ["tea"]
    pend = m.list("semantic")[0]
    assert pend["status"] == "pending" and m.search("Maria") == []        # pending rules unchanged
    m.confirm(pend["id"])
    assert m.search("Maria")[0]["value"] == SECRETS[2]
    assert m.get(m.list("profile")[0]["id"])["value"] == SECRETS[0]
    assert "jasmine" in m.export()


def test_keyed_dedupe_update_and_clear_work_when_encrypted(tmp_path, clock):
    m = MemoryStore(tmp_path / "m.db", "her", clock, PW)
    a = m.add({"category": "profile", "key": "pet", "value": "a cat"})
    b = m.add({"category": "profile", "key": "pet", "value": "a dog"})
    assert a == b and m.list("profile")[0]["value"] == "a dog"
    c = m.add({"category": "episodic", "value": "went to the park today"})
    d = m.add({"category": "episodic", "value": "Went to the park today"})
    assert c == d and len(m.list("episodic")) == 1
    m.update(a, {"value": "two cats", "key": "pets", "tags": ["x"]})
    r = m.list("profile")[0]
    assert (r["key"], r["value"], r["tags"]) == ("pets", "two cats", ["x"])
    assert m.add({"category": "profile", "key": "pets", "value": "three"}) == a
    assert m.clear("profile") == 1 and m.list("profile") == []


def test_wrong_missing_and_short_passphrase(tmp_path, clock):
    p = tmp_path / "m.db"
    MemoryStore(p, "her", clock, PW).close()
    with pytest.raises(EncryptionError, match="wrong passphrase"):
        MemoryStore(p, "her", clock, "another passphrase")
    with pytest.raises(EncryptionError, match="required"):
        MemoryStore(p, "her", clock)
    with pytest.raises(CryptoError):
        MemoryStore(tmp_path / "n.db", "her", clock, "short")
    MemoryStore(p, "her", clock, PW).close()                                # file is still usable


def test_plaintext_file_with_data_is_never_silently_mixed(tmp_path, clock):
    p = tmp_path / "m.db"
    m = MemoryStore(p, "her", clock)
    m.add({"category": "profile", "value": "plain"})
    m.close()
    with pytest.raises(EncryptionError, match="unencrypted"):
        MemoryStore(p, "her", clock, PW)


def test_values_cannot_be_swapped_between_rows_or_fields(tmp_path, clock):
    p = tmp_path / "m.db"
    m = MemoryStore(p, "her", clock, PW)
    a = m.add({"category": "episodic", "value": "first memory"})
    b = m.add({"category": "episodic", "value": "second memory"})
    m.close()
    db = sqlite3.connect(p)
    va = db.execute("SELECT value FROM memories WHERE id=?", (a,)).fetchone()[0]
    db.execute("UPDATE memories SET value=? WHERE id=?", (va, b))
    db.commit()
    db.close()
    m = MemoryStore(p, "her", clock, PW)
    with pytest.raises(CryptoError, match="integrity"):
        m.list()
    m.close()


def test_flipped_ciphertext_bit_is_detected(tmp_path, clock):
    p = tmp_path / "m.db"
    m = MemoryStore(p, "her", clock, PW)
    a = m.add({"category": "episodic", "value": "first memory"})
    m.close()
    db = sqlite3.connect(p)
    v = db.execute("SELECT value FROM memories").fetchone()[0]
    db.execute("UPDATE memories SET value=?", (v[:-3] + ("AAA" if not v.endswith("AAA") else "BBB"),))
    db.commit(); db.close()
    with pytest.raises(CryptoError):
        MemoryStore(p, "her", clock, PW).get(a)


def test_user_binding_still_enforced(tmp_path, clock):
    p = tmp_path / "m.db"
    MemoryStore(p, "her", clock, PW).close()
    with pytest.raises(WrongUser):
        MemoryStore(p, "him", clock, PW)


def test_reflection_vault_encrypts_and_keeps_its_rules(tmp_path, clock):
    p = tmp_path / "v.db"
    v = ReflectionVault(p, "her", clock, PW)
    v.set_enabled("reflection", True)
    with pytest.raises(MemoryError_):
        v.add({"category": "reflection", "value": SECRETS[3]})                 # consent still required
    v.add({"category": "reflection", "value": SECRETS[3]}, consent=True)
    v.close()
    assert SECRETS[3].encode() not in raw_bytes(p)
    v = ReflectionVault(p, "her", clock, PW)
    assert v.list("reflection")[0]["value"] == SECRETS[3]


def test_metadata_that_stays_visible_is_only_the_documented_set(tmp_path, clock):
    p = tmp_path / "m.db"
    m = MemoryStore(p, "her", clock, PW)
    fill(m)
    m.close()
    db = sqlite3.connect(p)
    cols = [r[1] for r in db.execute("PRAGMA table_info(memories)")]
    row = dict(zip(cols, db.execute("SELECT * FROM memories WHERE key IS NOT NULL").fetchone()))
    for f in ("key", "value", "tags", "source_context"):
        assert row[f].startswith("e1:")
    assert row["key_idx"] and "favorite" not in row["key_idx"]
    assert row["category"] == "profile"


def test_migration_encrypts_existing_file_and_verifies(tmp_path, clock):
    p = tmp_path / "m.db"
    m = MemoryStore(p, "her", clock)
    fill(m)
    before = [(x["id"], x["value"], x["key"]) for x in m.list()]
    m.close()
    assert SECRETS[0].encode() in raw_bytes(p)
    assert encrypt_existing(p, "her", PW, clock) == 3
    assert SECRETS[0].encode() not in raw_bytes(p) and not list(tmp_path.glob("*.enc-tmp"))
    m = MemoryStore(p, "her", clock, PW)
    assert [(x["id"], x["value"], x["key"]) for x in m.list()] == before
    assert m.add({"category": "profile", "key": "favorite_drink", "value": "coffee"}) == before[0][0]   # index works
    m.close()
    with pytest.raises(EncryptionError, match="already"):
        encrypt_existing(p, "her", PW, clock)
    with pytest.raises(WrongUser):
        q = tmp_path / "q.db"; MemoryStore(q, "her", clock).close(); encrypt_existing(q, "him", PW, clock)


def test_migration_failure_leaves_original_untouched(tmp_path, clock):
    p = tmp_path / "m.db"
    m = MemoryStore(p, "her", clock)
    fill(m); m.close()
    orig = raw_bytes(p)
    with pytest.raises(CryptoError):
        encrypt_existing(p, "her", "short", clock)
    assert raw_bytes(p) == orig and not list(tmp_path.glob("*.enc-tmp"))


def test_cli(tmp_path, clock, monkeypatch, capsys):
    p = tmp_path / "m.db"
    m = MemoryStore(p, "her", clock); fill(m); m.close()
    monkeypatch.setenv("NINETAILS_MEMORY_PASSPHRASE", PW)
    assert memory_crypt.main(["encrypt", str(p), "--user", "her"]) == 0
    assert memory_crypt.main(["encrypt", str(p), "--user", "her"]) == 1
    MemoryStore(p, "her", clock, PW).close()
