# CODEMAP

| Path | Purpose |
|---|---|
| `protocol/schema/envelope.json` | Envelope schema (version, id, type, payload, auth) |
| `protocol/schema/payloads.json` | Per-type payload schemas: `commands` (host->device), `events` (device->host) |
| `protocol/codec.py` | build/encode/decode/validate, size limit, version check, `ProtocolError` |
| `protocol/auth.py` | `Authenticator`: HMAC-SHA256, 30 s window, nonce replay cache |
| `protocol/examples/`, `compatibility.md` | Sample messages (tested), versioning rules |
| `simulator/device.py` | `SimulatedDevice`: power FSM, `Storage` (checksummed, atomic), commands, battery, dedupe |
| `companion/interfaces.py` | ABCs: DeviceTransport, LanguageModel, SpeechRecognizer, SpeechSynthesizer, MemoryStore, Clock |
| `companion/application/device_client.py` | `DeviceClient` (accepted vs completed, timeouts), `LoopbackTransport`, `backoff_delays` |
| `companion/personality/config.py` | Personality schema, `load`, `apply_update`, bounded `Personality.adapt` |
| `companion/scheduling/sleep.py` | `resolve_wake`, `build_sleep_payload`, ambiguity/clock errors |
| `companion/tamagotchi/rules.py` | Growth-rules schema + validation (`load`, `validate`) |
| `companion/tamagotchi/state.py` | `CharacterState` (needs, mood, events, stage growth), `StateStore` (checksummed persistence) |
| `companion/memory/store.py` | `MemoryStore` (profile/episodic/semantic/goal_habit), `ReflectionVault` (separate file, consent per write), user-bound files, retention, export |
| `companion/memory/extraction.py` | `extract_candidates` (rules) + `decide` save/ask/drop policy |
| `companion/behavior/engine.py` | `BehaviorEngine.react(stimulus)` -> `Plan` (animation, voice style, `Intent`s); `to_commands` -> validated `animation.play`; proactive gating |
| `species/<name>/personality.yaml` | Character configs (ninetales, cubone) |
| `species/ninetales/growth.yaml` | Needs rates, event effects, daily caps, stage requirements |
| `docs/` | architecture, decisions, hardware (unknowns), boundary, protocol, security, privacy, psychology-design, roadmap |
| `tests/` | pytest suites: protocol, firmware (simulated), companion |
| `firmware/`, `assets/`, `companion/{speech,conversation,psychology}` | Empty placeholders (.gitkeep) |
