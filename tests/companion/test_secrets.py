import json
import os
import stat
import pytest
from companion.security import InsecurePermissions, SecretStore, SecretStoreError
from tools.provision import main as provision_main

posix = pytest.mark.skipif(os.name != "posix", reason="POSIX file modes")
CA = "-----BEGIN CERTIFICATE-----\nMIIB\n-----END CERTIFICATE-----\n"


@pytest.fixture
def store(tmp_path, clock):
    return SecretStore(tmp_path / "keys.json", clock)


def test_create_device_returns_bundle_with_random_distinct_keys(store):
    a = store.create_device("robot-01", CA, "wss://h:1")
    b = store.create_device("robot-02", CA, "wss://h:1")
    assert a["v"] == 1 and a["kid"] == 1 and len(bytes.fromhex(a["key"])) == 32
    assert a["key"] != b["key"] and a["ca_pem"] == CA and a["device_id"] == "robot-01"


@pytest.mark.parametrize("bad", ["", "ab", "has space", "x" * 40, "../etc", "a;b"])
def test_bad_device_ids_rejected(store, bad):
    with pytest.raises(SecretStoreError):
        store.create_device(bad, CA, "wss://h")


def test_duplicate_active_device_refused_until_revoked(store):
    store.create_device("robot-01", CA, "wss://h")
    with pytest.raises(SecretStoreError):
        store.create_device("robot-01", CA, "wss://h")
    store.revoke("robot-01")
    new = store.create_device("robot-01", CA, "wss://h")      # re-provision after revoke gets a fresh key
    assert store.ring("robot-01").key(1).hex() == new["key"]


def test_listing_and_repr_never_expose_keys(store):
    b = store.create_device("robot-01", CA, "wss://h")
    assert b["key"] not in json.dumps(store.devices()) and b["key"] not in repr(store.ring("robot-01"))


def test_persists_and_reloads(tmp_path, clock):
    s = SecretStore(tmp_path / "k.json", clock)
    b = s.create_device("robot-01", CA, "wss://h")
    s2 = SecretStore(tmp_path / "k.json", clock)
    assert s2.ring("robot-01").key(1).hex() == b["key"]


@posix
def test_file_is_0600_and_loose_permissions_refused(tmp_path, clock):
    p = tmp_path / "k.json"
    SecretStore(p, clock).create_device("robot-01", CA, "wss://h")
    assert stat.S_IMODE(p.stat().st_mode) == 0o600
    p.chmod(0o644)
    with pytest.raises(InsecurePermissions):
        SecretStore(p, clock)
    p.chmod(0o600)
    SecretStore(p, clock)


def test_passphrase_encrypts_at_rest(tmp_path, clock):
    p = tmp_path / "k.json"
    b = SecretStore(p, clock, "correct horse").create_device("robot-01", CA, "wss://h")
    assert b["key"] not in p.read_text() and "robot-01" not in p.read_text()
    assert SecretStore(p, clock, "correct horse").ring("robot-01").key(1).hex() == b["key"]
    with pytest.raises(SecretStoreError, match="wrong passphrase"):
        SecretStore(p, clock, "nope")
    with pytest.raises(SecretStoreError, match="passphrase required"):
        SecretStore(p, clock)


def test_plain_file_contains_key_so_permissions_matter(tmp_path, clock):
    p = tmp_path / "k.json"
    b = SecretStore(p, clock).create_device("robot-01", CA, "wss://h")
    assert b["key"] in p.read_text()  # documented trade-off; use a passphrase on shared machines


def test_corrupt_store_fails_loudly_not_silently(tmp_path, clock):
    p = tmp_path / "k.json"
    p.write_text("{not json"); p.chmod(0o600)
    with pytest.raises(SecretStoreError):
        SecretStore(p, clock)


def test_revoke_destroys_key_material_and_blocks_gate(tmp_path, clock):
    p = tmp_path / "k.json"
    s = SecretStore(p, clock)
    b = s.create_device("robot-01", CA, "wss://h")
    s.revoke("robot-01")
    assert s.ring("robot-01") is None and b["key"] not in p.read_text()
    assert s.devices()[0]["status"] == "revoked"
    with pytest.raises(SecretStoreError):
        s.authenticator("robot-01", clock)
    assert SecretStore(p, clock).ring("robot-01") is None
    with pytest.raises(SecretStoreError):
        s.revoke("ghost")


def test_gate_accepts_known_device_marks_provisioned_and_rejects_others(store, clock):
    from protocol import Authenticator, build, encode, new_key
    store.create_device("robot-01", CA, "wss://h")
    gate = store.mac_gate(clock)
    good = store.authenticator("robot-01", clock).sign(build("battery.updated", {"percent": 1, "power_state": "AWAKE", "warning": True}))
    assert store.devices()[0]["provisioned_at"] is None
    assert gate(encode(good).decode())
    assert store.devices()[0]["provisioned_at"] is not None
    for bad in (Authenticator(new_key(), clock, "robot-01").sign(build("device.get_status")),
                Authenticator(new_key(), clock, "ghost-99").sign(build("device.get_status")),
                Authenticator(new_key(), clock).sign(build("device.get_status"))):
        assert not gate(encode(bad).decode())
    assert not gate("not json")


def test_begin_rotation_persists_before_use_and_blocks_second(tmp_path, clock):
    p = tmp_path / "k.json"
    s = SecretStore(p, clock)
    s.create_device("robot-01", CA, "wss://h")
    kid, key = s.begin_rotation("robot-01")
    assert kid == 2 and SecretStore(p, clock).ring("robot-01").key(2) == key
    with pytest.raises(SecretStoreError):
        s.begin_rotation("robot-01")


def test_cli_create_list_revoke(tmp_path, monkeypatch, capsys):
    ca = tmp_path / "ca.pem"; ca.write_text(CA)
    keys, out = tmp_path / "keys.json", tmp_path / "b.json"
    assert provision_main(["--store", str(keys), "create", "robot-01", "--url", "wss://h:1", "--ca", str(ca), "--out", str(out)]) == 0
    bundle = json.loads(out.read_text())
    assert bundle["device_id"] == "robot-01" and bundle["ca_pem"] == CA
    if os.name == "posix":
        assert stat.S_IMODE(out.stat().st_mode) == 0o600
    capsys.readouterr()
    assert provision_main(["--store", str(keys), "list"]) == 0
    listed = capsys.readouterr().out
    assert "robot-01" in listed and bundle["key"] not in listed
    assert provision_main(["--store", str(keys), "create", "robot-01", "--url", "wss://h", "--ca", str(ca)]) == 1
    assert provision_main(["--store", str(keys), "revoke", "robot-01"]) == 0
