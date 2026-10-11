"""A stand-in robot made of your computer: the SIMULATED device wired to a real microphone and speaker, so the whole
companion (Whisper, the language model, espeak, safety, memory) can be tried by voice before the hardware exists.
This is not hardware: no screen, no real battery, no real power behaviour.

    python -m tools.desk_robot init --dir dev --llm-url http://192.168.1.42:11434 --llm-model llama3.2:3b \
        --stt-model models/whisper-base.en
    python -m tools.desk_robot run --dir dev

While it runs: press ENTER = the robot's button (starts listening; ENTER again to stop early, or just stop talking).
Type  m  + ENTER to toggle the microphone (the physical mute),  q  + ENTER to quit.
Needs `arecord` and `aplay` (sudo apt install alsa-utils) and espeak-ng for the voice.
The `dev/` folder holds keys and your conversations' data. It is git-ignored; do not share it."""
from __future__ import annotations

import argparse
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path

import yaml

from companion.app import AppConfig, Companion, ConfigProblem
from companion.application.clock import SystemClock
from companion.application.ws_transport import client_ssl_context
from companion.security import SecretStore
from simulator import SimulatedDevice, Storage
from simulator.ws_link import DeviceLink
from tools.gen_certs import generate

DEVICE_ID = "desk-robot"
MIC_CHUNK = 3200                       # 100 ms of 16 kHz mono PCM16


class ArecordMic:
    def __init__(self):
        self.p = subprocess.Popen(["arecord", "-q", "-t", "raw", "-f", "S16_LE", "-r", "16000", "-c", "1"],
                                  stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)

    def read(self) -> bytes:
        return self.p.stdout.read(MIC_CHUNK) or b""

    def close(self) -> None:
        self.p.terminate()


class AplaySpeaker:
    def __init__(self):
        self.p = subprocess.Popen(["aplay", "-q", "-t", "raw", "-f", "S16_LE", "-r", "16000", "-c", "1"],
                                  stdin=subprocess.PIPE, stderr=subprocess.DEVNULL)

    def write(self, pcm: bytes) -> None:
        try:
            self.p.stdin.write(pcm)
            self.p.stdin.flush()
        except (BrokenPipeError, OSError):
            pass

    def close(self) -> None:
        try:
            self.p.stdin.close()
        except OSError:
            pass
        self.p.terminate()


def init(dir_: Path, llm_url: str, llm_model: str, stt_model: str | None, port: int = 8443, species: str = "ninetales") -> Path:
    dir_.mkdir(parents=True, exist_ok=True)
    cfg_path = dir_ / "companion.yaml"
    if cfg_path.exists():
        raise FileExistsError(f"{cfg_path} already exists; delete the folder to start over")
    pki = generate(dir_ / "tls", ["localhost", "127.0.0.1"])
    store = SecretStore(dir_ / "keys.json", time.time)
    bundle = store.create_device(DEVICE_ID, pki["ca_cert"].read_text(), f"wss://localhost:{port}")
    dev = SimulatedDevice(None, Storage(dir_ / "flash.json"), time.time)
    dev.provision(bundle)
    cfg = {"species": species, "device_id": DEVICE_ID, "secrets": "keys.json", "data_dir": "data",
           "tls": {"cert": str(pki["server_cert"].resolve()), "key": str(pki["server_key"].resolve())},
           "listen": {"host": "127.0.0.1", "port": port},
           "stt": {"engine": "whisper", "model": str(Path(stt_model).resolve())} if stt_model else {"engine": "none"},
           "tts": {"engine": "espeak"}, "llm": {"engine": "ollama", "url": llm_url, "model": llm_model},
           "memory": {"encrypt": False}}
    cfg_path.write_text(yaml.safe_dump(cfg, sort_keys=False))
    return cfg_path


def run(dir_: Path, mic=None, speaker=None, keys: "queue.Queue[str] | None" = None, stop: threading.Event | None = None,
        out=print, **companion_kwargs) -> int:
    dir_ = Path(dir_)
    cfg = AppConfig.load(dir_ / "companion.yaml")
    clock = SystemClock()
    app = Companion(cfg, clock=clock, **companion_kwargs)
    stop = stop or threading.Event()
    own_mic, own_spk = mic is None, speaker is None
    mic = mic or ArecordMic()
    speaker = speaker or AplaySpeaker()
    dev = SimulatedDevice(None, Storage(dir_ / "flash.json"), clock)
    dev.on_speaker = speaker.write
    dev.boot()
    bundle_ca = str(dir_ / "tls" / "ca.pem")
    link = DeviceLink(dev, f"wss://localhost:{cfg.port}", client_ssl_context(bundle_ca), tick_s=0.02)
    keys = keys or queue.Queue()
    host = threading.Thread(target=app.run, args=(stop,), name="companion", daemon=True)

    def pump_mic():
        while not stop.is_set():
            chunk = mic.read()
            if not chunk:
                if stop.wait(0.05):
                    return
                continue
            if dev.capture is not None:                      # the mic only matters while the device has it open
                link.call(lambda c=chunk: dev.feed_mic(c))

    threads = [host, threading.Thread(target=pump_mic, name="mic", daemon=True)]
    for t in threads:
        t.start()
    link.start()
    out("Desk robot is up. ENTER = button, m = mute toggle, q = quit.")
    try:
        while not stop.is_set():
            try:
                k = keys.get(timeout=0.1)
            except queue.Empty:
                continue
            if k == "q":
                break
            if k == "m":
                link.inject(lambda: dev.press_button("long"))
                out("(mic toggled)")
            else:
                link.inject(lambda: dev.press_button("single"))
    finally:
        stop.set()
        link.stop()
        for t in threads:
            t.join(5)
        if own_mic:
            mic.close()
        if own_spk:
            speaker.close()
    return 0


def _stdin_keys(q: "queue.Queue[str]") -> None:
    for line in sys.stdin:
        q.put(line.strip().lower())
    q.put("q")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    i = sub.add_parser("init")
    i.add_argument("--dir", default="dev")
    i.add_argument("--llm-url", required=True)
    i.add_argument("--llm-model", required=True)
    i.add_argument("--stt-model")
    i.add_argument("--species", default="ninetales")
    r = sub.add_parser("run")
    r.add_argument("--dir", default="dev")
    a = ap.parse_args(argv)
    try:
        if a.cmd == "init":
            p = init(Path(a.dir), a.llm_url, a.llm_model, a.stt_model, species=a.species)
            print(f"created {p}. Next: python -m tools.desk_robot run --dir {a.dir}")
            return 0
        q: "queue.Queue[str]" = queue.Queue()
        threading.Thread(target=_stdin_keys, args=(q,), daemon=True).start()
        return run(Path(a.dir), keys=q)
    except (ConfigProblem, FileExistsError, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
