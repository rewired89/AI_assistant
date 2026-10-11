"""Host-side firmware signing. The private key stays offline on the owner's machine (0600); never on the robot
and never used by the running companion service: it only pushes packages that were signed beforehand."""
from __future__ import annotations

import base64
import datetime as dt
import json
import os
from dataclasses import dataclass
from pathlib import Path

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

from protocol.ota import OtaFormatError, canonical, sha256_hex, verify_signature


@dataclass
class Package:
    manifest: dict
    signature: str
    image: bytes

    def save(self, directory: Path | str, name: str | None = None) -> tuple[Path, Path]:
        d = Path(directory)
        d.mkdir(parents=True, exist_ok=True)
        base = name or f"nine-tails-{self.manifest['version']}-b{self.manifest['build']}"
        img, man = d / f"{base}.bin", d / f"{base}.manifest.json"
        img.write_bytes(self.image)
        man.write_text(json.dumps({"manifest": self.manifest, "signature": self.signature}, indent=2, sort_keys=True))
        return img, man

    @classmethod
    def load(cls, manifest_path: Path | str) -> "Package":
        mp = Path(manifest_path)
        doc = json.loads(mp.read_text())
        img = mp.with_name(mp.name.replace(".manifest.json", ".bin"))
        return cls(doc["manifest"], doc["signature"], img.read_bytes())


def generate_signing_key(path: Path | str) -> bytes:
    """Writes a new private key (PEM, 0600) and returns the public key DER to trust on devices."""
    path = Path(path)
    if path.exists():
        raise FileExistsError(f"{path} already exists; refusing to overwrite a signing key")
    key = ec.generate_private_key(ec.SECP256R1())
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    return public_der(key)


def public_der(key: ec.EllipticCurvePrivateKey) -> bytes:
    return key.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)


def load_signing_key(path: Path | str) -> ec.EllipticCurvePrivateKey:
    key = serialization.load_pem_private_key(Path(path).read_bytes(), password=None)
    if not isinstance(key, ec.EllipticCurvePrivateKey) or key.curve.name != "secp256r1":
        raise OtaFormatError("signing key must be ECDSA P-256")
    return key


def build_package(image: bytes, key: ec.EllipticCurvePrivateKey, *, product: str, board: str, version: str, build: int,
                  sign_kid: int = 1, requires_build: int = 0, created: str | None = None) -> Package:
    manifest = {"product": product, "board": board, "version": version, "build": build, "size": len(image),
                "sha256": sha256_hex(image), "created": created or dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
                "sign_kid": sign_kid}
    if requires_build:
        manifest["requires_build"] = requires_build
    sig = base64.b64encode(key.sign(canonical(manifest), ec.ECDSA(hashes.SHA256()))).decode()
    return Package(manifest, sig, image)


def check_package(pkg: Package, trusted: dict[int, bytes]) -> None:
    """Host-side sanity check before pushing: right size and hash, and signed by a key the device trusts."""
    m = pkg.manifest
    if m["size"] != len(pkg.image) or m["sha256"] != sha256_hex(pkg.image):
        raise OtaFormatError("image does not match its manifest")
    der = trusted.get(m["sign_kid"])
    if der is None or not verify_signature(m, pkg.signature, der):
        raise OtaFormatError("manifest signature is not valid for a trusted key")
