"""Create a private CA and a server certificate for local wss:// use. Cross-platform (python-cryptography).

    python -m tools.gen_certs --out secrets/tls --host ninetails.local --host 192.168.1.50

The device pins ca.pem (public). Keep ca.key and server.key private; `secrets/` is git-ignored.
ECDSA P-256 keeps handshakes light for the ESP32. Server cert validity is 825 days.
"""
from __future__ import annotations

import argparse
import datetime as dt
import ipaddress
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID


def _key():
    return ec.generate_private_key(ec.SECP256R1())


def _write_key(path: Path, key) -> None:
    path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                       serialization.NoEncryption()))
    try:
        path.chmod(0o600)
    except OSError:
        pass  # Windows


def _name(cn: str) -> x509.Name:
    return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])


def generate(out: Path, hosts: list[str], now: dt.datetime | None = None) -> dict[str, Path]:
    out.mkdir(parents=True, exist_ok=True)
    now = now or dt.datetime.now(dt.timezone.utc)
    ca_key, srv_key = _key(), _key()
    ca = (x509.CertificateBuilder().subject_name(_name("Nine Tails Local CA")).issuer_name(_name("Nine Tails Local CA"))
          .public_key(ca_key.public_key()).serial_number(x509.random_serial_number())
          .not_valid_before(now - dt.timedelta(minutes=5)).not_valid_after(now + dt.timedelta(days=3650))
          .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
          .add_extension(x509.KeyUsage(False, False, False, False, False, True, True, False, False), critical=True)
          .sign(ca_key, hashes.SHA256()))
    sans = []
    for h in hosts:
        try:
            sans.append(x509.IPAddress(ipaddress.ip_address(h)))
        except ValueError:
            sans.append(x509.DNSName(h))
    srv = (x509.CertificateBuilder().subject_name(_name(hosts[0])).issuer_name(ca.subject)
           .public_key(srv_key.public_key()).serial_number(x509.random_serial_number())
           .not_valid_before(now - dt.timedelta(minutes=5)).not_valid_after(now + dt.timedelta(days=825))
           .add_extension(x509.SubjectAlternativeName(sans), critical=False)
           .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
           .add_extension(x509.ExtendedKeyUsage([x509.oid.ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
           .sign(ca_key, hashes.SHA256()))
    paths = {"ca_cert": out / "ca.pem", "ca_key": out / "ca.key", "server_cert": out / "server.pem",
             "server_key": out / "server.key"}
    paths["ca_cert"].write_bytes(ca.public_bytes(serialization.Encoding.PEM))
    paths["server_cert"].write_bytes(srv.public_bytes(serialization.Encoding.PEM))
    _write_key(paths["ca_key"], ca_key)
    _write_key(paths["server_key"], srv_key)
    return paths


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="secrets/tls")
    ap.add_argument("--host", action="append", default=None, help="DNS name or IP; repeat for several")
    a = ap.parse_args()
    for k, v in generate(Path(a.out), a.host or ["localhost", "127.0.0.1"]).items():
        print(f"{k}: {v}")
