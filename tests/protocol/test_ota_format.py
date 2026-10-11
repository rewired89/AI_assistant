import base64
import copy
import json
import stat

import pytest

from companion.security.ota_signing import (OtaFormatError, Package, build_package, check_package, generate_signing_key,
                                            load_signing_key)
from protocol import ProtocolError, build, validate_payload
from protocol.ota import canonical, sha256_hex, verify_signature
from tools import ota as cli


@pytest.fixture
def kp(tmp_path):
    pub = generate_signing_key(tmp_path / "k.key")
    return load_signing_key(tmp_path / "k.key"), pub, tmp_path


def make(key, **kw):
    return build_package(b"x" * 5000, key, product="nine-tails", board="b", version="1.0.0", build=3, **kw)


def test_signature_roundtrip_and_tamper(kp):
    key, pub, _ = kp
    p = make(key)
    assert verify_signature(p.manifest, p.signature, pub)
    for field, val in [("build", 4), ("sha256", "0" * 64), ("size", 1), ("board", "c"), ("version", "9.9.9")]:
        m = dict(p.manifest, **{field: val})
        assert not verify_signature(m, p.signature, pub), field
    m = dict(p.manifest, extra=1)
    assert not verify_signature(m, p.signature, pub)
    assert not verify_signature(p.manifest, "not base64!!", pub)
    assert not verify_signature(p.manifest, base64.b64encode(b"x" * 64).decode(), pub)
    assert not verify_signature(p.manifest, p.signature, b"junk")


def test_canonical_is_key_order_independent():
    assert canonical({"a": 1, "b": 2}) == canonical({"b": 2, "a": 1})


def test_check_package(kp):
    key, pub, _ = kp
    p = make(key)
    check_package(p, {1: pub})
    with pytest.raises(OtaFormatError):
        check_package(Package(p.manifest, p.signature, p.image + b"!"), {1: pub})
    with pytest.raises(OtaFormatError):
        check_package(p, {2: pub})
    other = generate_signing_key(kp[2] / "o.key")
    with pytest.raises(OtaFormatError):
        check_package(p, {1: other})


def test_private_key_file_is_private_and_never_overwritten(kp):
    _, _, d = kp
    assert stat.S_IMODE((d / "k.key").stat().st_mode) == 0o600
    with pytest.raises(FileExistsError):
        generate_signing_key(d / "k.key")


def test_manifest_schema_rejects_bad_values(kp):
    key, _, _ = kp
    p = make(key)

    def cmd(m):
        validate_payload(build("ota.begin", {"update_id": "upd1", "stream_no": 5, "manifest": m, "signature": p.signature}), "command")
    cmd(p.manifest)
    for field, val in [("size", 9 * 1024 * 1024), ("size", 0), ("version", "latest"), ("sha256", "abc"), ("build", 0)]:
        with pytest.raises(ProtocolError):
            cmd(dict(p.manifest, **{field: val}))
    with pytest.raises(ProtocolError):
        cmd(dict(p.manifest, surprise=1))


def test_cli_roundtrip(kp, capsys):
    _, _, d = kp
    assert cli.main(["keygen", str(d / "c.key"), "--pub", str(d / "c.pub")]) == 0
    (d / "fw.bin").write_bytes(b"firmware" * 1000)
    assert cli.main(["build", str(d / "fw.bin"), "--key", str(d / "c.key"), "--version", "0.2.0", "--build", "2",
                     "--board", "b", "--out", str(d / "dist")]) == 0
    man = d / "dist" / "nine-tails-0.2.0-b2.manifest.json"
    assert cli.main(["verify", str(man), "--pub", str(d / "c.pub")]) == 0
    assert cli.main(["info", str(man)]) == 0
    img = man.with_name("nine-tails-0.2.0-b2.bin")
    img.write_bytes(img.read_bytes() + b"x")
    assert cli.main(["verify", str(man), "--pub", str(d / "c.pub")]) == 1
    assert cli.main(["keygen", str(d / "c.key"), "--pub", str(d / "c.pub")]) == 1      # refuses to overwrite
