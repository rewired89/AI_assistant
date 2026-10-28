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


def set_stt(config: Path, model: Path) -> None:
    """Turn speech recognition on in an existing companion.yaml, pointing at a downloaded Whisper model folder. Changes nothing else."""
    model = model.expanduser().resolve()
    if not (model.is_dir() and any(model.iterdir())):
        raise ValueError(f"{model} is not a downloaded model folder (run: python -m tools.fetch_models whisper --size base.en --out models/whisper-base.en)")
    cfg = yaml.safe_load(config.read_text())
    if not isinstance(cfg, dict):
        raise ValueError(f"{config} is not a companion config")
    cfg["stt"] = {"engine": "whisper", "model": str(model)}
    config.write_text(yaml.safe_dump(cfg, sort_keys=False))


def set_tts(config: Path, model: Path, voice: str = "af_heart") -> None:
    """Switch the voice to Kokoro (natural female voices) in an existing companion.yaml. Changes nothing else."""
    model = model.expanduser().resolve()
    if not ((model / "kokoro-v1.0.onnx").is_file() and (model / "voices-v1.0.bin").is_file()):
        raise ValueError(f"{model} does not hold the voice files (run: python -m tools.fetch_models kokoro --out models/kokoro)")
    cfg = yaml.safe_load(config.read_text())
    if not isinstance(cfg, dict):
        raise ValueError(f"{config} is not a companion config")
    cfg["tts"] = {"engine": "kokoro", "model": str(model), "voice": voice}
    config.write_text(yaml.safe_dump(cfg, sort_keys=False))


def service_unit(config: Path, python: str, user: str, root: Path) -> str:
    """A systemd unit that starts the companion at boot from a repository checkout (python -m companion needs the repo as its working folder)."""
    config, root = config.expanduser().resolve(), root.expanduser().resolve()
    if not config.is_file():
        raise ValueError(f"{config} does not exist")
    return f"""[Unit]
Description=Nine Tails companion host
After=network-online.target ollama.service
Wants=network-online.target

[Service]
User={user}
WorkingDirectory={root}
Environment=PYTHONUNBUFFERED=1
ExecStart={python} -m companion --config {config}
Restart=always
RestartSec=5
NoNewPrivileges=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
"""


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
    t = sub.add_parser("set-stt", help="switch her speech recognition (ears) on in an existing config")
    t.add_argument("--config", required=True)
    t.add_argument("--model", required=True, help="the folder made by `python -m tools.fetch_models whisper`")
    v = sub.add_parser("set-tts", help="switch her voice to the natural female voice (Kokoro) in an existing config")
    v.add_argument("--config", required=True)
    v.add_argument("--model", required=True, help="the folder made by `python -m tools.fetch_models kokoro`")
    v.add_argument("--voice", default="af_heart", help="af_heart (default), af_sarah, af_bella, af_nicole, af_sky, af_nova, bf_emma")
    w = sub.add_parser("service", help="write a systemd unit that starts her host at boot (run from the repository folder)")
    w.add_argument("--config", required=True)
    w.add_argument("--python", default=sys.executable, help="the python of the .venv (default: the one running this command)")
    w.add_argument("--user", default=getpass.getuser())
    w.add_argument("--out", help="where to write the unit (default: next to the config)")
    a = ap.parse_args(argv)
    if a.cmd == "service":
        try:
            text = service_unit(Path(a.config), a.python, a.user, Path.cwd())
        except (ValueError, OSError) as e:
            print(f"error: {e}", file=sys.stderr)
            return 1
        out = Path(a.out) if a.out else Path(a.config).expanduser().resolve().parent / "ninetails.service"
        out.write_text(text)
        print(f"Wrote {out}. Install it (it then starts at every boot and restarts if it crashes):")
        print(f"  sudo cp {out} /etc/systemd/system/ninetails.service")
        print("  sudo systemctl daemon-reload")
        print("  sudo systemctl enable --now ninetails")
        print("Watch it:  journalctl -u ninetails -f      Stop it:  sudo systemctl stop ninetails")
        return 0
    if a.cmd == "set-tts":
        try:
            set_tts(Path(a.config), Path(a.model), a.voice)
        except (ValueError, OSError, yaml.YAMLError) as e:
            print(f"error: {e}", file=sys.stderr)
            return 1
        print(f"Her voice is now {a.voice} (Kokoro) in {a.config}. Restart the companion for it to take effect.")
        return 0
    if a.cmd == "set-stt":
        try:
            set_stt(Path(a.config), Path(a.model))
        except (ValueError, OSError, yaml.YAMLError) as e:
            print(f"error: {e}", file=sys.stderr)
            return 1
        print(f"Speech recognition is now ON in {a.config} (model {Path(a.model).expanduser().resolve()}). Restart the companion for it to take effect.")
        return 0
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
