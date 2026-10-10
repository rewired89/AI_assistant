"""First-run onboarding: two core questions, then optional ones offered slowly, one at a time.

Scripted (no LLM, no network). Every answer is validated, applied to a config *patch* (so species
updates still flow) and persisted; restarts never repeat answered steps. Skipping is always fine,
nothing is nagged, and enabling the reflection helper never enables the sensitive-memory vault.
"""
from __future__ import annotations

import copy
import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from ..personality import config as pc
from ..storage import ChecksumJSON

CORE = ["user_name", "companion_name"]
OPTIONAL = ["communication_style", "notification_frequency", "sleep_behavior", "memory_preference",
            "psychology_optin", "voice", "privacy_info"]
CORE_LAPSE_S, OPTIONAL_LAPSE_S, DEFER_S, FIRST_OFFER_DELAY_S = 600, 1800, 86400, 1800
MAX_ATTEMPTS = 2

CONFIG_EFFECTS = {
    "communication_style": {
        "short": {"communication": {"verbosity": "low", "humor": "light"}},
        "chatty": {"communication": {"verbosity": "high", "humor": "moderate"}},
        "playful": {"communication": {"vocabulary_style": "cute", "humor": "high", "emotional_expression": "high"}},
        "calm": {"communication": {"verbosity": "medium", "humor": "light", "emotional_expression": "medium",
                                   "vocabulary_style": "plain"}}},
    "notification_frequency": {
        "never": {"behavior": {"proactive_questions": "never", "unsolicited_greetings": False}},
        "rarely": {"behavior": {"proactive_questions": "rare", "unsolicited_greetings": True}},
        "sometimes": {"behavior": {"proactive_questions": "occasional", "unsolicited_greetings": True}}},
}
IDLE_SLEEP_S = {"soon": 120, "while": 300, "stay": 1800}
MEMORY_CATEGORIES = {"remember": {"profile", "episodic", "semantic", "goal_habit"},
                     "minimal": {"profile", "semantic", "goal_habit"}, "nothing": set()}
ALL_CATEGORIES = ("profile", "episodic", "semantic", "goal_habit")
NOT_NAMES = {"yes", "no", "hi", "hello", "hey", "ok", "okay", "what", "who", "tired", "fine", "good", "great",
             "sad", "happy", "sorry", "hungry", "bored", "here", "back", "ready", "sure", "nothing"}
_NAME_PAT = re.compile(r"(?:you can call me|call me|my name is|name'?s|i'?m|i am|it'?s)\s+(.+)", re.I)
REQUIRED_COPY = ["user_name", "companion_name", "core_done", "later", "skipped", "communication_style",
                 "notification_frequency", "sleep_behavior", "memory_preference", "psychology_optin", "voice",
                 "privacy_info"]


@dataclass
class Turn:
    kind: str                       # ask | reask | ack | passthrough | no_pending
    step: str | None = None
    text: str = ""
    passthrough: bool = False       # caller should treat the utterance as normal conversation
    follow_up: "Turn | None" = None


def _merge(dst: dict, src: dict) -> dict:
    for k, v in src.items():
        if isinstance(v, dict) and isinstance(dst.get(k), dict):
            _merge(dst[k], v)
        else:
            dst[k] = copy.deepcopy(v)
    return dst


def load_copy(species_dir: str | Path) -> dict:
    base = yaml.safe_load((Path(__file__).parent / "onboarding_default.yaml").read_text())
    override = Path(species_dir) / "onboarding.yaml"
    if override.exists():
        _merge(base, yaml.safe_load(override.read_text()) or {})
    missing = [k for k in REQUIRED_COPY if k not in base["steps"]]
    if missing:
        raise ValueError(f"onboarding copy missing {missing}")
    return base


def parse_name(text: str) -> str | None:
    m = _NAME_PAT.search(text)
    cand = (m.group(1) if m else text).strip().strip(".!?,\"' ")
    words = cand.split()
    if not 1 <= len(words) <= 3 or len(cand) > 32:
        return None
    for w in words:
        if not all(ch.isalpha() or ch in "'-" for ch in w) or not w[0].isalpha():
            return None
    if cand.lower() in NOT_NAMES or any(w.lower() in NOT_NAMES for w in words if len(words) == 1):
        return None
    return " ".join(w[:1].upper() + w[1:] if w.islower() else w for w in words)


def _has(text: str, tokens: set[str], words: list[str]) -> bool:
    low = text.lower()
    return any((w in low) if " " in w else (w in tokens) for w in words)


class Onboarding:
    def __init__(self, path: Path | str, copy_: dict, base_config: dict, memory=None, device=None,
                 voices: list[dict] | None = None, privacy_text: str | None = None,
                 min_gap_s: int = 4 * 3600):
        self.copy, self.base, self.memory, self.device = copy_["steps"], pc.validate(copy.deepcopy(base_config)), memory, device
        self.copy_all = copy_
        self.voices, self.privacy_text, self.min_gap_s = voices or [], privacy_text, min_gap_s
        self.store = ChecksumJSON(path)
        self.s = self.store.load(self._fresh())
        self.recovered = self.store.recovered
        self.reconcile_memory()

    @staticmethod
    def _fresh() -> dict:
        return {"version": 1, "core_done": False, "current": None, "answers": {}, "skipped": [],
                "deferred": {}, "next_offer_at": 0, "config_patch": {}, "settings": {}, "device_pending": {}}

    def _save(self) -> None:
        self.store.save(self.s)

    # ---- read-only views ------------------------------------------------
    @property
    def core_done(self) -> bool:
        return self.s["core_done"]

    def awaiting(self) -> str | None:
        return self.s["current"]["step"] if self.s["current"] else None

    def answers(self) -> dict:
        return dict(self.s["answers"])

    def user_name(self) -> str | None:
        return self.s["answers"].get("user_name")

    def effective_config(self) -> dict:
        return pc.apply_update(self.base, self.s["config_patch"], pc.USER_SETTINGS) if self.s["config_patch"] else copy.deepcopy(self.base)

    def _companion_name(self) -> str:
        return self.effective_config()["character"]["name"]

    # ---- driving the flow -----------------------------------------------
    def next_prompt(self, now: float, quiet: bool = False, sleeping: bool = False) -> Turn | None:
        cur = self.s["current"]
        if cur:
            lapse = CORE_LAPSE_S if cur["step"] in CORE else OPTIONAL_LAPSE_S
            if now - cur["asked_at"] < lapse:
                return None  # already asked, still waiting
            if cur["step"] in CORE:
                cur["asked_at"] = now
                self._save()
                return self._turn("reask", cur["step"])
            self._lapse_optional(cur, now)
        if not self.s["core_done"]:
            step = next(st for st in CORE if st not in self.s["answers"] and st not in self.s["skipped"])
            return self._ask(step, now)
        if quiet or sleeping or now < self.s["next_offer_at"]:
            return None
        for step in OPTIONAL:
            if step in self.s["answers"] or step in self.s["skipped"] or self.s["deferred"].get(step, 0) > now:
                continue
            if step == "voice" and not self.voices:
                continue
            if step == "privacy_info" and not self.privacy_text:
                continue
            self.s["next_offer_at"] = now + self.min_gap_s
            if step == "privacy_info":  # informational: nothing to wait for
                self.s["answers"][step] = True
                self._save()
                return self._turn("ask", step)
            return self._ask(step, now)
        return None

    def _lapse_optional(self, cur: dict, now: float) -> None:
        self.s["current"] = None
        if cur["attempts"] + 1 >= MAX_ATTEMPTS:
            self.s["skipped"].append(cur["step"])
        else:
            self.s["deferred"][cur["step"]] = now + DEFER_S
        self._save()

    def _ask(self, step: str, now: float) -> Turn:
        self.s["current"] = {"step": step, "asked_at": now, "attempts": 0}
        self._save()
        return self._turn("ask", step)

    def reopen(self, step: str, now: float) -> Turn:
        """Settings-screen path: ask again (or for the first time) regardless of pacing."""
        if step not in CORE + OPTIONAL:
            raise ValueError(step)
        self.s["skipped"] = [x for x in self.s["skipped"] if x != step]
        self.s["deferred"].pop(step, None)
        return self._ask(step, now)

    def answer(self, text: str, now: float) -> Turn:
        cur = self.s["current"]
        if not cur:
            return Turn("no_pending", passthrough=True)
        step = cur["step"]
        text = text.strip()[:300]
        tokens = set(re.findall(r"[\w']+", text.lower()))
        c = self.copy_all
        is_name = step in CORE
        if not is_name and step != "psychology_optin" and _has(text, tokens, c["later_words"]):
            return self._finish_defer(step, now)
        if is_name and text.lower() in c["skip_words"] or (not is_name and step != "psychology_optin"
                                                           and _has(text, tokens, c["skip_words"])):
            return self._finish_skip(step, now)
        value = self._parse(step, text, tokens)
        if value is None:
            if step == "psychology_optin" and _has(text, tokens, c["later_words"] + c["skip_words"]):
                return self._finish_defer(step, now)
            return self._fail(cur, step, now)
        return self._accept(step, value, now)

    # ---- parsing ----------------------------------------------------------
    def _options(self, step: str) -> dict:
        if step == "voice":
            return {v["id"]: {"label": v["label"], "keywords": [t for t in re.findall(r"[\w']+", v["label"].lower())]}
                    for v in self.voices}
        return self.copy[step].get("options", {})

    def _parse(self, step: str, text: str, tokens: set[str]):
        c = self.copy_all
        if step == "user_name":
            return parse_name(text)
        if step == "companion_name":
            same = text.strip(" .!").lower() == self._companion_name().lower()
            if same or (not _NAME_PAT.search(text) and len(tokens) <= 4 and _has(text, tokens, c["keep_words"])):
                return self._companion_name()
            return parse_name(text)
        if step == "psychology_optin":
            yes, no = _has(text, tokens, self.copy[step]["yes_words"]), _has(text, tokens, self.copy[step]["no_words"])
            return "yes" if yes and not no else "no" if no and not yes else None
        opts = self._options(step)
        for i, key in enumerate(opts, 1):  # "2" picks the second option
            if text.strip(" .") == str(i):
                return key
        scores = {k: len(tokens & set(o["keywords"])) for k, o in opts.items()}
        best = max(scores.values(), default=0)
        winners = [k for k, v in scores.items() if v == best]
        return winners[0] if best > 0 and len(winners) == 1 else None

    # ---- outcomes -----------------------------------------------------------
    def _fail(self, cur: dict, step: str, now: float) -> Turn:
        cur["attempts"] += 1
        if cur["attempts"] < MAX_ATTEMPTS:
            cur["asked_at"] = now
            self._save()
            return self._turn("reask", step)
        self.s["current"] = None
        if step in CORE:  # never block setup on a name
            self.s["skipped"].append(step)
            return self._core_progress(Turn("ack", step, self._fmt(self.copy["skipped"])), now)
        self.s["deferred"][step] = now + DEFER_S
        self._save()
        return Turn("passthrough", step, passthrough=True)

    def _finish_skip(self, step: str, now: float) -> Turn:
        self.s["current"] = None
        self.s["skipped"].append(step)
        t = Turn("ack", step, self._fmt(self.copy["skipped"] if step not in CORE else self.copy[step].get("ack_skip", self.copy["skipped"])))
        return self._core_progress(t, now) if step in CORE else (self._save() or t)

    def _finish_defer(self, step: str, now: float) -> Turn:
        self.s["current"] = None
        self.s["deferred"][step] = now + DEFER_S
        self._save()
        return Turn("ack", step, self._fmt(self.copy["later"]))

    def _accept(self, step: str, value: str, now: float) -> Turn:
        try:
            label = self._apply(step, value)
        except pc.ConfigError:
            return self._fail(self.s["current"], step, now)
        self.s["answers"][step] = value
        self.s["current"] = None
        c = self.copy[step]
        if step == "psychology_optin":
            text = c["ack_yes" if value == "yes" else "ack_no"]
        elif step == "companion_name" and value == self.base["character"]["name"] and "ack_keep" in c:
            text = c["ack_keep"]
        else:
            text = c.get("ack", "Okay.")
        t = Turn("ack", step, self._fmt(text, value=value, label=label))
        return self._core_progress(t, now) if step in CORE else (self._save() or t)

    def _core_progress(self, turn: Turn, now: float) -> Turn:
        done = all(st in self.s["answers"] or st in self.s["skipped"] for st in CORE)
        if done and not self.s["core_done"]:
            self.s["core_done"] = True
            self.s["next_offer_at"] = now + FIRST_OFFER_DELAY_S
            turn.text = (turn.text + " " + self._fmt(self.copy["core_done"])).strip()
        self._save()
        if not done:
            turn.follow_up = self.next_prompt(now)
        return turn

    # ---- applying answers ------------------------------------------------------
    def _patch(self, patch: dict) -> None:
        trial = copy.deepcopy(self.s["config_patch"])
        _merge(trial, patch)
        pc.apply_update(self.base, trial, pc.USER_SETTINGS)  # raises ConfigError if invalid
        self.s["config_patch"] = trial

    def _apply(self, step: str, value: str) -> str:
        label = self._options(step).get(value, {}).get("label", value)
        if step == "user_name":
            if self.memory:
                self._remember("user_name", value)
        elif step == "companion_name":
            self._patch({"character": {"name": value}})
        elif step in CONFIG_EFFECTS:
            self._patch(CONFIG_EFFECTS[step][value])
        elif step == "sleep_behavior":
            self.s["device_pending"]["idle_sleep_seconds"] = IDLE_SLEEP_S[value]
            self.sync_device()
        elif step == "memory_preference":
            self.s["settings"]["memory_preference"] = value
            self.s["answers"][step] = value
            self.reconcile_memory()
        elif step == "psychology_optin":
            # Availability of the helper only. The sensitive vault stays off until she consents per save.
            self._patch({"behavior": {"psychological_growth_features": "on" if value == "yes" else "off"}})
        elif step == "voice":
            self.s["settings"]["voice_id"] = value
        return label

    def _remember(self, key: str, value: str) -> None:
        from ..memory import Disabled, MemoryError_
        try:
            self.memory.add({"category": "profile", "key": key, "value": value,
                             "source": "explicit_user_statement", "source_context": "onboarding"})
        except (Disabled, MemoryError_):
            pass  # the settings store still holds the name; memory is best-effort

    def reconcile_memory(self) -> None:
        pref = self.s["settings"].get("memory_preference")
        if self.memory and pref:
            allowed = MEMORY_CATEGORIES[pref]
            for cat in ALL_CATEGORIES:
                self.memory.set_enabled(cat, cat in allowed)

    # ---- device sync --------------------------------------------------------------
    def pending_device_config(self) -> dict:
        return dict(self.s["device_pending"])

    def sync_device(self) -> bool:
        """Push pending device settings. Offline/sleeping devices keep them pending for next connect."""
        if not self.s["device_pending"]:
            return True
        if self.device is None:
            return False
        from protocol import ProtocolError
        from .device_client import CommandRejected, CommandTimeout
        try:
            self.device.request("device.set_config", {"config": dict(self.s["device_pending"])}, timeout_s=3)
        except (CommandTimeout, CommandRejected, ProtocolError):
            self._save()
            return False
        self.s["device_pending"] = {}
        self._save()
        return True

    # ---- text -------------------------------------------------------------------
    def _fmt(self, template: str, **kw) -> str:
        return template.format(companion_name=self._companion_name(), user_name=self.user_name() or "friend",
                               voices=", ".join(v["label"] for v in self.voices),
                               privacy_text=self.privacy_text or "", **kw) if "{" in template else template

    def _turn(self, kind: str, step: str) -> Turn:
        c = self.copy[step]
        key = "ask" if kind == "ask" else "reask"
        return Turn(kind, step, self._fmt(c.get(key, c["ask"])))
