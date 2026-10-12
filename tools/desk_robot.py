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
from companion.speech.vad import rms
from simulator import SimulatedDevice, Storage
from simulator.ws_link import DeviceLink
from tools.desk_view import ViewServer
from tools.gen_certs import generate

DEVICE_ID = "desk-robot"
MIC_CHUNK = 3200                       # 100 ms of 16 kHz mono PCM16


class ArecordMic:
    """Reads the microphone through `arecord`. Unlike a plain pipe it NEVER fails silently: the program's error text is
    kept, a dead or silent microphone is reported by `status()`, and one restart is attempted if it dies."""

    def __init__(self, device: str | None = None, argv: list[str] | None = None, silence_after_s: float = 3.0):
        self.device = device
        self.argv = argv or (["arecord", "-q", "-t", "raw", "-f", "S16_LE", "-r", "16000", "-c", "1"]
                             + (["-D", device] if device else []))
        self.silence_after_s = silence_after_s
        self.err: list[str] = []
        self.restarts = 0
        self.last_data = time.time()
        self.started = time.time()
        self.p = None
        self._start()

    def _start(self) -> None:
        try:
            self.p = subprocess.Popen(self.argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        except OSError as e:
            self.p = None
            self.err.append(f"cannot start {self.argv[0]}: {e.strerror or type(e).__name__} (sudo apt install alsa-utils)")
            return
        threading.Thread(target=self._drain_stderr, args=(self.p,), daemon=True, name="mic-stderr").start()

    def _drain_stderr(self, p) -> None:
        for line in iter(p.stderr.readline, b""):
            self.err = (self.err + [line.decode(errors="replace").strip()])[-5:]

    def read(self) -> bytes:
        if self.p is None:
            return b""
        data = self.p.stdout.read(MIC_CHUNK)
        if data:
            self.last_data = time.time()
            return data
        if self.p.poll() is not None and self.restarts < 1:        # it died: try once more, keep what it said
            self.restarts += 1
            self._start()
        return b""

    def status(self) -> str:
        """'ok', or the reason there is no microphone audio (shown on screen and in the terminal)."""
        if self.p is None:
            return self.err[-1] if self.err else "microphone program not started"
        if self.p.poll() is not None:
            return "microphone program stopped" + (f": {self.err[-1]}" if self.err else "")
        if time.time() - max(self.last_data, self.started) > self.silence_after_s:
            return "no audio from the microphone" + (f": {self.err[-1]}" if self.err else " (wrong input device? try --mic-device)")
        return "ok"

    def close(self) -> None:
        if self.p is not None:
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


def init(dir_: Path, llm_url: str, llm_model: str, stt_model: str | None, port: int = 8443, species: str = "ninetales",
         tts_model: str | None = None, voice: str = "af_heart") -> Path:
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
           "tts": ({"engine": "kokoro", "model": str(Path(tts_model).resolve()), "voice": voice} if tts_model
                   else {"engine": "espeak"}), "llm": {"engine": "ollama", "url": llm_url, "model": llm_model},
           "memory": {"encrypt": False}}
    cfg_path.write_text(yaml.safe_dump(cfg, sort_keys=False))
    return cfg_path


def run(dir_: Path, mic=None, speaker=None, keys: "queue.Queue[str] | None" = None, stop: threading.Event | None = None,
        out=print, view_port: int | None = 8765, mic_device: str | None = None, **companion_kwargs) -> int:
    dir_ = Path(dir_)
    cfg = AppConfig.load(dir_ / "companion.yaml")
    clock = SystemClock()
    app = Companion(cfg, clock=clock, **companion_kwargs)
    stop = stop or threading.Event()
    own_mic, own_spk = mic is None, speaker is None
    mic = mic or ArecordMic(mic_device)
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
            meter["level"] = rms(chunk)
            if dev.capture is not None:                      # the mic only matters while the device has it open
                link.call(lambda c=chunk: dev.feed_mic(c))

    meter = {"level": 0.0, "status": "ok"}
    heard = {"text": "", "empty": False}

    def on_heard(text: str) -> None:
        heard["text"], heard["empty"] = text, not text
    app.pipeline.on_heard = on_heard

    def snapshot() -> dict:
        pipe = app.pipeline
        caption = ""
        sp = getattr(pipe, "_speech", None)
        if pipe.state.value == "speaking" and sp:
            item = sp["sentences"][min(sp["i"], len(sp["sentences"]) - 1)]
            caption = item if isinstance(item, str) else ""
        if not link.connected:
            anim = "offline"
        elif dev.power.value == "SLEEPING":
            anim = "sleeping"
        else:
            anim = dev.animation["name"] if dev.animation else "idle"
        det = getattr(pipe, "_det", None)
        return {"anim": anim, "power": dev.power.value, "battery": dev.battery, "mic": bool(dev.state["config"]["mic_enabled"]),
                "pipe": pipe.state.value, "stage": app.state.stage, "caption": caption,
                "speaking": pipe.state.value == "speaking", "notes": [n for _, n in list(pipe.log)[-5:]],
                "level": meter["level"], "threshold": det.current_threshold if det else 0,
                "heard": heard["text"], "heard_empty": heard["empty"], "mic_status": meter["status"]}

    def command(cmd: str, arg) -> None:
        if cmd == "press":
            link.inject(lambda: dev.press_button("single"))
        elif cmd == "mute":
            link.inject(lambda: dev.press_button("long"))
        elif cmd == "wake":
            link.inject(lambda: dev.press_button("double"))
        elif cmd == "battery":
            link.inject(lambda: dev.set_battery(int(arg)))

    view = ViewServer(snapshot, command, port=view_port) if view_port is not None else None
    threads = [host, threading.Thread(target=pump_mic, name="mic", daemon=True)]
    for t in threads:
        t.start()
    link.start()
    if view:
        out(f"Her face: open {view.start()} in your browser (SPACE there = the button).")
    out("Desk robot is up. ENTER = button, m = mute toggle, q = quit.")
    seen = 0
    try:
        while not stop.is_set():
            seen = _print_new_notes(app.pipeline, seen, out)
            status = mic.status() if hasattr(mic, "status") else "ok"
            if status != meter["status"]:
                meter["status"] = status
                out(f"  [mic] {status}" if status != "ok" else "  [mic] audio is arriving again")
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
        if view:
            view.stop()
        link.stop()
        for t in threads:
            t.join(5)
        if own_mic:
            mic.close()
        if own_spk:
            speaker.close()
    return 0


def _print_new_notes(pipe, seen: int, out) -> int:
    """Shows what she is doing (state names only, never what was said) so a silent robot can be diagnosed."""
    total = pipe.notes_total
    if total > seen:
        items = list(pipe.log)
        for st, note in items[-min(total - seen, len(items)):]:
            out(f"  [{st}] {note}")
    return total


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
    i.add_argument("--tts-model", help="folder from tools.fetch_models kokoro (natural voice); omit for robotic espeak")
    i.add_argument("--voice", default="af_heart")
    r = sub.add_parser("run")
    r.add_argument("--dir", default="dev")
    r.add_argument("--mic-device", help="arecord device name, from: arecord -l   e.g. plughw:1,0")
    r.add_argument("--view-port", type=int, default=8765, help="0 = pick a free port, -1 = no face window")
    a = ap.parse_args(argv)
    try:
        if a.cmd == "init":
            p = init(Path(a.dir), a.llm_url, a.llm_model, a.stt_model, species=a.species, tts_model=a.tts_model, voice=a.voice)
            print(f"created {p}. Next: python -m tools.desk_robot run --dir {a.dir}")
            return 0
        q: "queue.Queue[str]" = queue.Queue()
        threading.Thread(target=_stdin_keys, args=(q,), daemon=True).start()
        return run(Path(a.dir), keys=q, view_port=None if a.view_port < 0 else a.view_port, mic_device=a.mic_device)
    except (ConfigProblem, FileExistsError, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
