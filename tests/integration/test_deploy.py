import json
import stat

import pytest
import yaml

from companion.app import AppConfig
from tools import deploy


def test_init_writes_a_valid_deployment(tmp_path):
    p = deploy.build(tmp_path / "nt", "192.168.1.42", user="nine", python="/opt/nt/.venv/bin/python")
    cfg = AppConfig.load(p["config"])
    assert cfg.port == 8443
    c = yaml.safe_load(p["config"].read_text())
    assert c["listen"]["host"] == "0.0.0.0" and c["llm"]["url"].startswith("http://127.0.0.1")
    unit = p["unit"].read_text()
    assert "User=nine" in unit and f"--config {p['config']}" in unit and "ProtectSystem=strict" in unit
    assert stat.S_IMODE(p["bundle"].stat().st_mode) == 0o600
    b = json.loads(p["bundle"].read_text())
    assert "192.168.1.42" in json.dumps(b)
    from cryptography import x509
    san = x509.load_pem_x509_certificate((p["dir"] / "tls" / "server.pem").read_bytes()).extensions.get_extension_for_class(
        x509.SubjectAlternativeName).value
    assert [str(i) for i in san.get_values_for_type(x509.IPAddress)] == ["192.168.1.42"]


def test_refuses_existing_folder_and_public_ip(tmp_path):
    (tmp_path / "x").mkdir()
    (tmp_path / "x" / "keep.txt").write_text("mine")
    with pytest.raises(FileExistsError):
        deploy.build(tmp_path / "x", "192.168.1.42")
    assert (tmp_path / "x" / "keep.txt").read_text() == "mine"
    with pytest.raises(ValueError):
        deploy.build(tmp_path / "y", "8.8.8.8")
