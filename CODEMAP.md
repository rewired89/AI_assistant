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
| `companion/storage.py` | `ChecksumJSON` atomic checksummed JSON (used by onboarding; older stores have their own copies) |
| `companion/application/onboarding.py` | `Onboarding` flow: core questions, paced optional steps, answer parsing, effects, device sync; `load_copy`, `parse_name` |
| `companion/application/onboarding_default.yaml`, `species/*/onboarding.yaml` | Scripted copy + option keywords (species deep-merges over default) |
| `companion/application/proactive.py` | `ProactiveDirector.tick()`: stimuli/idle -> Intents -> composed speech; pending queue with TTL; memory reuse; periodic state save |
| `companion/conversation/dialogue.py`, `species/*/dialogue.yaml` | Scripted line per Intent kind (fallback + no-LLM mode), validated placeholders, `LinePicker` |
| `companion/conversation/engine.py` | `ConversationEngine.respond()` -> `Reply`; confirmations, memory decisions, sleep handling, history (RAM) |
| `companion/conversation/{prompt,safety,tools}.py` | System prompt builder; output validators + `speechify`; tool specs + `parse_tool_calls` |
| `companion/conversation/{ollama,fakes}.py` | Local-only Ollama adapter; scripted `FakeLanguageModel` |
| `companion/speech/pipeline.py` | `VoicePipeline.poll()` state machine (IDLE/LISTENING/TRANSCRIBING/THINKING/SPEAKING), `Reply`, `Responder`, `split_sentences`, `clean_transcript` |
| `companion/speech/vad.py`, `fakes.py`, `lines.py` | `EndpointDetector`/`rms`; fake STT/TTS; scripted state lines (`species/*/speech.yaml`) |
| `protocol/audio.py` | Binary audio frame `pack`/`unpack` (strict), constants, `pcm_seconds` |
| `protocol/keys.py` | `KeyRing` (kid->key, signing kid, adopt/retire rules, no key in repr) |
| `companion/security/secrets.py` | `SecretStore`: per-device keys, bundle creation, revoke, begin_rotation, `mac_gate`; 0600 + optional scrypt/Fernet |
| `companion/security/rotation.py` | `rotate_device_key` (resume-safe) |
| `tools/provision.py` | CLI: create (bundle) / list / revoke |
| `companion/application/ws_transport.py` | `WebSocketHostTransport` (wss server in a thread, `DeviceTransport`), `mac_gate`, ssl context helpers, `TokenBucket` |
| `companion/application/clock.py` | `SystemClock` |
| `simulator/ws_link.py` | `DeviceLink`: simulated device dials host over wss, reconnect/backoff, offline ticking, sleep closes socket |
| `tools/gen_certs.py` | Private CA + server cert generator (ECDSA P-256), cross-platform |
| `tests/companion/test_proactive.py` | Director + compose_intent end to end with sim device and voice pipeline |
| `tests/companion/test_conversation.py`, `test_conversation_voice.py` | Safety/tools/prompt/engine/Ollama stub; LLM inside the voice pipeline |
| `tests/companion/test_voice_pipeline.py`, `test_speech_basics.py`, `tests/integration/test_voice_wss.py` | Voice turns end to end (sim + real TLS), cancel/barge-in, failures, privacy |
| `tests/protocol/test_audio_frames.py`, `tests/firmware/test_audio_sim.py`, `tests/integration/test_wss_audio.py` | Frame format + fuzz; sim capture/playback rules; audio over real TLS |
| `tests/integration/test_wss.py` | Real TLS loopback tests: roundtrip, wrong CA/host, pre-auth, flood, reconnect, sleep/wake |
| `species/<name>/personality.yaml` | Character configs (ninetales, cubone) |
| `species/ninetales/growth.yaml` | Needs rates, event effects, daily caps, stage requirements |
| `docs/` | architecture, decisions, hardware (unknowns), boundary, protocol, security, privacy, psychology-design, roadmap |
| `tests/` | pytest suites: protocol, firmware (simulated), companion |
| `firmware/`, `assets/`, `companion/{speech,conversation,psychology}` | Empty placeholders (.gitkeep) |
