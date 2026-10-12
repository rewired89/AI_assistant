"""Logic behind the sprite workbench (no web code here): the asset catalog, the consistency checks, a no-hardware
interaction simulator that uses the REAL behaviour engine, and the rebuild of a sprite set from its source PNGs.

Honesty rules: a clip with no frames is reported missing, never replaced by another sprite; every simulated event is
labelled SIMULATED; nothing here talks to a device or a language model."""
from __future__ import annotations

import glob
import json
import random
from datetime import timezone
from pathlib import Path

from . import sprites as sp
from . import sprites_import as si
from .behavior import BehaviorEngine
from .conversation.dialogue import LinePicker, load_templates
from .personality import config as pc
from .tamagotchi import CharacterState, load_rules

ROOT = Path(__file__).resolve().parent.parent
SPRITES = ROOT / "assets" / "sprites"
STATUSES = ("real_frames", "composed", "placeholder", "missing", "not_applicable")
MANIFEST_STATUSES = ("composed", "placeholder", "missing")
KINDS = ("protocol", "workbench")


class ManifestError(ValueError):
    pass


# ---------------------------------------------------------------------------------------------- manifest
def load_manifest(path: Path | str | None = None) -> dict:
    path = Path(path) if path else SPRITES / "manifest.json"
    try:
        m = json.loads(path.read_text())
    except (OSError, ValueError) as e:
        raise ManifestError(f"cannot read {path}: {e}") from None
    return validate_manifest(m)


def validate_manifest(m: object) -> dict:
    if not isinstance(m, dict) or m.get("version") != 1:
        raise ManifestError("manifest must be an object with version 1")
    cats, anims, sets = m.get("categories"), m.get("animations"), m.get("sets")
    if not isinstance(cats, list) or not cats or not all(isinstance(c, str) for c in cats):
        raise ManifestError("categories must be a list of names")
    if not isinstance(anims, dict) or not anims:
        raise ManifestError("animations must be an object")
    if not isinstance(sets, dict) or not sets:
        raise ManifestError("sets must be an object")
    for aid, a in anims.items():
        if aid not in sp.KNOWN_ANIMATIONS:
            raise ManifestError(f"{aid}: not a known animation id")
        if not isinstance(a, dict) or a.get("category") not in cats or a.get("kind") not in KINDS \
                or a.get("status") not in MANIFEST_STATUSES or not isinstance(a.get("label"), str):
            raise ManifestError(f"{aid}: needs category (one of {cats}), kind, status, label")
        if a["kind"] == "protocol" and aid not in sp.ANIMATIONS:
            raise ManifestError(f"{aid}: marked protocol but the device protocol has no such animation")
        if a["kind"] == "workbench" and aid in sp.ANIMATIONS:
            raise ManifestError(f"{aid}: marked workbench but it is a protocol animation")
        if not all(s in sets for s in a.get("real_in", [])):
            raise ManifestError(f"{aid}: real_in names an unknown set")
    for name, s in sets.items():
        if not isinstance(s, dict) or not isinstance(s.get("label"), str):
            raise ManifestError(f"set {name}: needs a label")
        if not all(x in anims for x in s.get("not_applicable", [])):
            raise ManifestError(f"set {name}: not_applicable names an unknown animation")
    return m


def status_in(manifest: dict, set_name: str, aid: str, present: bool) -> str:
    """The honest status of one animation in one set."""
    if aid in manifest["sets"][set_name].get("not_applicable", []):
        return "not_applicable"
    if not present:
        return "missing"
    a = manifest["animations"][aid]
    if a["status"] == "missing":
        return "missing"
    if a["status"] == "composed" and set_name in a.get("real_in", []):
        return "real_frames"
    return a["status"]


# ---------------------------------------------------------------------------------------------- catalog
def set_files(sprite_dir: Path | str = SPRITES) -> dict[str, Path]:
    d = Path(sprite_dir)
    return {p.stem: p for p in sorted(d.glob("*.json")) if p.name != "manifest.json"}


def catalog(sprite_dir: Path | str = SPRITES, manifest: dict | None = None) -> dict:
    manifest = manifest or load_manifest(Path(sprite_dir) / "manifest.json")
    sets = []
    for name, f in set_files(sprite_dir).items():
        if name not in manifest["sets"]:
            continue
        try:
            doc = sp.load(f)
            err = None
        except sp.SpriteError as e:
            doc, err = None, str(e)
        rows = []
        for aid, meta in manifest["animations"].items():
            a = doc["animations"].get(aid) if doc else None
            total = sum(fr["ms"] for fr in a["frames"]) if a else 0
            rows.append({"id": aid, "label": meta["label"], "category": meta["category"], "kind": meta["kind"],
                         "status": status_in(manifest, name, aid, a is not None), "note": meta.get("note", ""),
                         "frames": len(a["frames"]) if a else 0, "loop": a.get("loop", True) if a else None,
                         "total_ms": total, "avg_ms": round(total / len(a["frames"])) if a else 0,
                         "width": doc["width"] if a else None, "height": doc["height"] if a else None})
        sets.append({"name": name, "label": manifest["sets"][name]["label"], "file": f.name, "error": err,
                     "width": doc["width"] if doc else None, "height": doc["height"] if doc else None,
                     "colors": len(doc["palette"]) if doc else None, "animations": rows})
    return {"categories": manifest["categories"], "screens": manifest.get("screens", []), "sets": sets}


def fingerprint(sprite_dir: Path | str = SPRITES) -> str:
    """Changes whenever a sprite file, the manifest or a source picture changes: the page reloads when it does."""
    d = Path(sprite_dir)
    parts = []
    for p in sorted(list(d.glob("*.json")) + list(d.glob("source*/**/*.png"))):
        try:
            st = p.stat()
            parts.append(f"{p.name}:{st.st_mtime_ns}:{st.st_size}")
        except OSError:
            pass
    return "|".join(parts)


# ---------------------------------------------------------------------------------------------- checks
def check(sprite_dir: Path | str = SPRITES) -> list[str]:
    """Every problem with the sprite assets (empty list = clean): missing assets, broken frame references, bad definitions,
    and a manifest that claims art which does not exist (or denies art that does)."""
    d = Path(sprite_dir)
    out: list[str] = []
    try:
        m = load_manifest(d / "manifest.json")
    except ManifestError as e:
        return [f"manifest: {e}"]
    files = set_files(d)
    for name, meta in m["sets"].items():
        if name not in files:
            out.append(f"set {name}: file {name}.json is missing")
            continue
        try:
            doc = sp.load(files[name])
        except sp.SpriteError as e:
            out.append(f"set {name}: {e}")
            continue
        if name == "default":
            for aid in sorted(sp.ANIMATIONS - set(doc["animations"])):
                out.append(f"set default: protocol animation '{aid}' has no frames")
        for aid, a in m["animations"].items():
            present = aid in doc["animations"]
            na = aid in meta.get("not_applicable", [])
            if na:
                if present:
                    out.append(f"set {name}: '{aid}' is listed not_applicable but has frames")
                continue
            if a["status"] == "missing" and present:
                out.append(f"set {name}: manifest says '{aid}' is missing but frames exist; update the manifest")
            if a["status"] != "missing" and not present and aid in sp.ANIMATIONS:
                out.append(f"set {name}: '{aid}' is claimed as {a['status']} but has no frames")
        for aid in doc["animations"]:
            if aid not in m["animations"]:
                out.append(f"set {name}: animation '{aid}' is not described in the manifest")
        src = meta.get("source_dir")
        if src:
            for v in si.VIEWS:
                if not (d / src / f"{v}.png").exists():
                    out.append(f"set {name}: source picture {src}/{v}.png is missing (the set cannot be rebuilt)")
    for name in files:
        if name not in m["sets"]:
            out.append(f"{name}.json is not listed in the manifest")
    return out


# ---------------------------------------------------------------------------------------------- rebuild from PNGs
def rebuild_set(name: str, sprite_dir: Path | str = SPRITES, manifest: dict | None = None) -> str:
    """Rebuilds assets/sprites/<name>.json from its source PNGs (the same step as tools.import_sprites). Returns a message."""
    d = Path(sprite_dir)
    m = manifest or load_manifest(d / "manifest.json")
    meta = m["sets"].get(name)
    if not meta or not meta.get("source_dir"):
        raise sp.SpriteError(f"set {name} has no source pictures")
    src = d / meta["source_dir"]
    views = {v: src / f"{v}.png" for v in si.VIEWS}
    idle = sorted(glob.glob(str(d / meta["idle_frames"]))) if meta.get("idle_frames") else None
    doc = si.build_set(views, name, idle_frames=idle or None, extras=not meta.get("not_applicable"))
    sp.save(d / f"{name}.json", doc)
    return f"rebuilt {name}.json from {meta['source_dir']}/ ({len(doc['animations'])} animations)"


def stale_sets(sprite_dir: Path | str = SPRITES, manifest: dict | None = None) -> list[str]:
    """Sets whose source PNGs are newer than their JSON (someone replaced a picture)."""
    d = Path(sprite_dir)
    m = manifest or load_manifest(d / "manifest.json")
    out = []
    for name, meta in m["sets"].items():
        j = d / f"{name}.json"
        src = meta.get("source_dir")
        if not src or not j.exists():
            continue
        pngs = [d / src / f"{v}.png" for v in si.VIEWS]
        if meta.get("idle_frames"):
            pngs += [Path(p) for p in glob.glob(str(d / meta["idle_frames"]))]
        newest = max((p.stat().st_mtime_ns for p in pngs if p.exists()), default=0)
        if newest > j.stat().st_mtime_ns:
            out.append(name)
    return out


# ---------------------------------------------------------------------------------------------- simulator
MOODS = {  # state values that make the REAL mood function return that mood
    "calm": {}, "happy": {"happiness": 0.9}, "playful": {"playfulness": 0.9, "energy": 0.9}, "curious": {"curiosity": 0.9},
    "sleepy": {"energy": 0.1}, "subdued": {"happiness": 0.3}}
SAMPLE_MESSAGES = ["[sample message] Hi! How are you today?", "[sample message] I had a long day.", "[sample message] Do you want to play?"]
SAMPLE_REPLIES = ["[sample reply] Hi! I'm glad you're here.", "[sample reply] That sounds like a lot. Want to tell me about it?",
                  "[sample reply] Yes! Let's play something."]


class Simulator:
    """Feeds made-up events to the real BehaviorEngine and says which animation, voice style and scripted line come out.
    No device, no model, no network. Everything it returns is marked simulated."""

    def __init__(self, species: str = "ninetales", mood: str = "calm", root: Path | str = ROOT, seed: int = 1):
        self.root = Path(root)
        self.species = species if species in self.profiles(self.root) else "ninetales"
        if mood not in MOODS:
            raise ValueError(f"unknown mood {mood!r}")
        self.mood = mood
        self.now = 1_800_000_000.0
        sp_dir = self.root / "species" / self.species
        growth = sp_dir / "growth.yaml"
        rules = load_rules(growth if growth.exists() else self.root / "species" / "ninetales" / "growth.yaml")
        self.state = CharacterState(rules, self.now, timezone.utc)
        for k, v in MOODS[mood].items():
            setattr(self.state, k, v)
        self.state.affection = self.state.trust = 0.5                     # "familiar" so greetings use her name
        self.p = pc.Personality(pc.load(sp_dir / "personality.yaml"))
        self.engine = BehaviorEngine(self.p, self.state, lambda: self.now, random.Random(seed))
        self.lines = LinePicker(load_templates(sp_dir), random.Random(seed))

    @staticmethod
    def profiles(root: Path | str = ROOT) -> list[str]:
        return sorted(p.name for p in (Path(root) / "species").iterdir() if (p / "personality.yaml").exists())

    def _line(self, intent) -> str:
        t = self.lines.pick(intent.kind, self.state.relationship_state)
        return t.format(name="friend", fact="you like foxes", stage_word="fox")

    def _step(self, anim: str, label: str, hold_ms: int, **extra) -> dict:
        return {"animation": anim, "label": label, "hold_ms": hold_ms, **extra}

    def fire(self, event: str) -> dict:
        """Returns {"simulated": True, "event", "steps": [{animation,label,hold_ms,dialogue?,voice_style?}], "notes": [...]}."""
        self.now += 1.0
        steps: list[dict] = []
        notes: list[str] = []
        plan = None
        if event.startswith("emotion:"):
            name = event.split(":", 1)[1]
            if name not in sp.KNOWN_ANIMATIONS:
                raise ValueError(f"unknown animation {name!r}")
            steps.append(self._step(name, f"manual: {name}", 3000))
        elif event == "message":
            steps += [self._step("listening", "message arrives (sample): she is heard", 1500, dialogue=self._pick(SAMPLE_MESSAGES)),
                      self._step("thinking", "thinking (no real model is asked)", 1800),
                      self._step("talking", "speaking a sample reply", 3000, dialogue=self._pick(SAMPLE_REPLIES))]
            notes.append("Sample text only. A real reply comes from the language model once one is configured.")
        elif event == "speak":
            steps.append(self._step("talking", "speaking (sample)", 3000, dialogue=self._pick(SAMPLE_REPLIES)))
        elif event == "listen":
            plan = self.engine.react("listening_start")
        elif event == "think":
            plan = self.engine.react("thinking")
        elif event in ("wake", "wake_scheduled"):
            plan = self.engine.react("wake", cause="scheduled" if event == "wake_scheduled" else "button",
                                     sleep_reason="battery_saving" if event == "wake_scheduled" else None)
        elif event == "sleep":
            plan = self.engine.react("sleep_entering")
        elif event == "battery_warning":
            plan = self.engine.react("battery_warning")
        elif event == "battery_low":
            plan = self.engine.react("battery_low")
            notes.append("The behaviour engine asks the robot for 'sleepy' here. The 'low_battery' clip exists in the sprite set "
                         "but the device protocol does not carry that name yet.")
            steps.append(self._step("low_battery", "workbench-only clip: low battery look", 3000))
        elif event == "touch":
            plan = self.engine.react("touch")
        elif event == "idle":
            plan = self.engine.react("idle_tick")
        elif event == "offline":
            plan = self.engine.react("host_disconnected")
        else:
            raise ValueError(f"unknown event {event!r}")
        if plan is not None:
            if plan.animation:
                steps.insert(0, self._step(plan.animation["name"], f"behaviour engine: {plan.animation['name']}", 3000,
                                           voice_style=plan.voice_style))
            for intent in plan.intents:
                steps.append(self._step(steps[-1]["animation"] if steps else "talking", f"says ({intent.kind})", 3000,
                                        dialogue=self._line(intent), voice_style=intent.tone))
            if not steps:
                notes.append("The engine chose to do nothing here (for example, touch while asleep is ignored).")
        return {"simulated": True, "event": event, "species": self.species, "mood": self.state.mood, "steps": steps, "notes": notes}

    def _pick(self, options: list[str]) -> str:
        return options[int(self.now) % len(options)]


# ---------------------------------------------------------------------------------------------- what the web page calls
class WorkbenchApi:
    """Everything the workbench page asks for, as plain functions (the HTTP layer only forwards)."""

    def __init__(self, sprite_dir: Path | str = SPRITES, root: Path | str = ROOT):
        self.dir, self.root = Path(sprite_dir), Path(root)
        self._sim: Simulator | None = None
        self._last_errors: list[str] = []

    def catalog(self) -> dict:
        c = catalog(self.dir)
        c["profiles"] = Simulator.profiles(self.root)
        c["moods"] = sorted(MOODS)
        c["problems"] = check(self.dir)
        return c

    def sprites(self, name: str) -> dict:
        files = set_files(self.dir)
        if name not in files:
            raise KeyError(name)
        return sp.load(files[name])

    def refresh(self) -> dict:
        """Called every second or two by the page. If a source picture was replaced, rebuild that set; then report whether
        anything changed since the last call (the page compares the fingerprint)."""
        rebuilt, errors = [], []
        try:
            for name in stale_sets(self.dir):
                try:
                    rebuilt.append(rebuild_set(name, self.dir))
                except (sp.SpriteError, OSError, ManifestError) as e:
                    errors.append(f"{name}: {e}")
        except ManifestError as e:
            errors.append(f"manifest: {e}")
        self._last_errors = errors
        return {"fingerprint": fingerprint(self.dir), "rebuilt": rebuilt, "errors": errors}

    def simulate(self, body: dict) -> dict:
        species, mood, event = body.get("species", "ninetales"), body.get("mood", "calm"), body.get("event")
        if not isinstance(event, str) or not isinstance(species, str) or not isinstance(mood, str):
            raise ValueError("event, species and mood must be text")
        if event == "reset" or self._sim is None or (self._sim.species, self._sim.mood) != (species, mood):
            self._sim = Simulator(species, mood, self.root)
            if event == "reset":
                return {"simulated": True, "event": "reset", "species": self._sim.species, "mood": self._sim.state.mood,
                        "steps": [], "notes": ["Simulated robot reset to a fresh state."]}
        return self._sim.fire(event)
