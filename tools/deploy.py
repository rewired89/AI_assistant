"""Set up the companion host as a service on the always-on computer (the Minisforum). Writes everything into one folder.

    python -m tools.deploy init --dir ~/nine-tails --host-ip 192.168.1.42 --stt-model models/whisper-base.en --tts-model models/kokoro

Creates: TLS certificates for that IP, the key file, the robot's provisioning bundle (flash it over USB, then delete it),
companion.yaml (Ollama on the same machine), and ninetails.service for systemd. It never overwrites an existing folder.
It does not start or install anything; the printed commands do, and you run them."""
from __future__ import annotations

import argparse
import getpass
import ipaddress
import os
import json
import sys
import time
from pathlib import Path

import yaml

from companion.security import SecretStore
from tools.gen_certs import generate

PORT = 8443


def build(dir_: Path, host_ip: str, device_id: str = "ninetails-01", species: str = "ninetales", llm_model: str = "llama3.2:3b",
          stt_model: str | None = None, tts_model: str | None = None, voice: str = "af_heart", user: str | None = None,
          python: str | None = None, hostname: str | None = None) -> dict[str, Path]:
    ip = ipaddress.ip_address(host_ip)
    if not ip.is_private:
        raise ValueError("host-ip must be a home-network address (like 192.168.1.42)")
    dir_ = Path(dir_).expanduser().resolve()
    if dir_.exists() and any(dir_.iterdir()):
        raise FileExistsError(f"{dir_} is not empty; choose a new folder (nothing was changed)")
    dir_.mkdir(parents=True, exist_ok=True)
    os.chmod(dir_, 0o700)
    hosts = [str(ip)] + ([hostname] if hostname else [])
    pki = generate(dir_ / "tls", hosts)
    store = SecretStore(dir_ / "keys.json", time.time)
    bundle = store.create_device(device_id, pki["ca_cert"].read_text(), f"wss://{ip}:{PORT}")
    bundle_path = dir_ / f"{device_id}.bundle.json"
    fd = os.open(bundle_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(bundle, f, indent=2)
    cfg = {"species": species, "device_id": device_id, "secrets": "keys.json", "data_dir": "data",
           "tls": {"cert": "tls/server.pem", "key": "tls/server.key"},
           "listen": {"host": "0.0.0.0", "port": PORT},
           "stt": {"engine": "whisper", "model": str(Path(stt_model).expanduser().resolve())} if stt_model else {"engine": "none"},
           "tts": ({"engine": "kokoro", "model": str(Path(tts_model).expanduser().resolve()), "voice": voice} if tts_model
                   else {"engine": "espeak"}),
           "llm": {"engine": "ollama", "url": "http://127.0.0.1:11434", "model": llm_model},
           "memory": {"encrypt": False}}
    cfg_path = dir_ / "companion.yaml"
    cfg_path.write_text(yaml.safe_dump(cfg, sort_keys=False))
    unit = dir_ / "ninetails.service"
    unit.write_text(f"""[Unit]
Description=Nine Tails companion host
After=network-online.target ollama.service
Wants=network-online.target

[Service]
User={user or getpass.getuser()}
WorkingDirectory={dir_}
ExecStart={python or sys.executable} -m companion --config {cfg_path}
Restart=on-failure
RestartSec=5
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=read-only
ReadWritePaths={dir_}

[Install]
WantedBy=multi-user.target
""")
    return {"dir": dir_, "config": cfg_path, "unit": unit, "bundle": bundle_path, "ca": pki["ca_cert"]}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    i = sub.add_parser("init")
    i.add_argument("--dir", required=True)
    i.add_argument("--host-ip", required=True)
    i.add_argument("--hostname", help="optional extra name for the certificate, e.g. minisforum.local")
    i.add_argument("--device-id", default="ninetails-01")
    i.add_argument("--species", default="ninetales")
    i.add_argument("--llm-model", default="llama3.2:3b")
    i.add_argument("--stt-model")
    i.add_argument("--tts-model")
    i.add_argument("--voice", default="af_heart")
    a = ap.parse_args(argv)
    try:
        p = build(Path(a.dir), a.host_ip, a.device_id, a.species, a.llm_model, a.stt_model, a.tts_model, a.voice, hostname=a.hostname)
    except (ValueError, FileExistsError, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(f"""Created {p['dir']}
Next, on this computer:
  python -m tools.check_llm --config {p['config']}
  python -m companion --config {p['config']}                    # try it in the foreground first (Ctrl+C stops)
Start on boot:
  sudo cp {p['unit']} /etc/systemd/system/ninetails.service
  sudo systemctl daemon-reload
  sudo systemctl enable --now ninetails
  journalctl -u ninetails -f                                     # logs
Allow only your home network to reach it (if ufw is on):
  sudo ufw allow from 192.168.1.0/24 to any port {PORT} proto tcp
The robot's key file is {p['bundle']}: flash it over USB, then DELETE it. {p['dir']}/tls/ca.key can sign certificates: keep it private.""")
    return 0


if __name__ == "__main__":
    sys.exit(main())
