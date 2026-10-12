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
| `companion/psychology/engine.py` | `PsychologyEngine.handle()`: routing, consent, sessions, listening mode, goals, notes, reminders |
| `companion/psychology/safety/{screen,gate,validator}.py` | Risk screen + scripted replies; `SafetyGate` (hold, 1 h caution); psychology output validator; `resources.yaml` |
| `companion/psychology/exercises/{library.yaml,library.py,runner.py}` | 14 exercises, validation against sources, step runner (skip/stop/time parsing) |
| `companion/psychology/conflict_navigation/guide.py`, `growth_plans/plans.py`, `settings.py`, `sources/` | Walk-through builder; goals in vault; content-free settings; evidence register |
| `tools/evidence_table.py`, `docs/psychology-evidence.md` | Generated evidence doc (test keeps it in sync) |
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
| `protocol/ota.py` | Manifest canonical bytes, SHA-256, P-256 signature verify (shared by host and device) |
| `companion/security/ota_signing.py` | Offline signing: `Package`, `build_package`, `check_package`, key generation (0600) |
| `companion/application/ota_updater.py` | `OtaUpdater`: push (windowed, resumable), apply, confirm, abort; `safe_to_apply` |
| `simulator/ota.py` | `OtaEngine`: A/B slots, verify, trial, rollback, anti-rollback floor |
| `tools/ota.py` | CLI: keygen / build / verify / info |
| `tests/protocol/test_ota_format.py`, `tests/firmware/test_ota.py`, `tests/integration/test_wss_ota.py` | OTA format, device rules, update over real TLS |
| `companion/speech/engines.py` | `WhisperRecognizer` (faster-whisper, local), `EspeakSynthesizer` (espeak-ng subprocess), wav parse + resample |
| `tools/bench_stt.py` | Benchmark local Whisper: load time, real-time factor, memory, transcript |
| `tools/check_llm.py`, `tests/companion/test_check_llm.py` | Check a remote Ollama server: reachable, model present, answer time |
| `tools/desk_robot.py`, `tests/integration/test_desk_robot.py` | Simulated robot wired to laptop mic/speaker; init + run the whole companion by voice |
| `tools/desk_view.py`, `tests/integration/test_desk_view.py` | Desk robot window: live face + mic meter + "You said", and the pixel sprite editor; local HTTP only, Host/Origin/header checked |
| `companion/sprites_import.py` (incl. `split_sheet`, real idle frames -> expressions), `tools/import_sprites.py`, `tests/companion/test_sprites_import.py`, `assets/sprites/source/` | PNG stills -> sprite set with generated motion and overlays; the owner's source art |
| `companion/sprites.py`, `companion/sprites_default.py`, `assets/sprites/default.json`, `tests/companion/test_sprites.py`, `docs/sprites.md` | Sprite set format (strict validation, atomic save), starter art generator, the committed default set |
| `tools/bench_tts.py` | Time a TTS voice and save a WAV to listen to |
| `companion/learning/routines.py`, `companion/learning/memory_talk.py`, `tests/companion/test_learning.py`, `docs/learning.md` | Routine recognition (evidence rules, pending until she says yes) and spoken "what do you remember / forget that" |
| `tools/mic_check.py`, `tests/integration/test_mic_tools.py` | 5-second microphone check with level bar and a plain verdict; ArecordMic reports failures instead of hiding them |
| `tools/fetch_models.py` | One-time Whisper model download |
| `tests/companion/test_speech_engines.py` | Engine adapters: stubbed process/model, real espeak through the pipeline |
| `companion/memory/crypto.py` | `FieldCipher`: AES-GCM field encryption + HMAC key index (scrypt/HKDF) |
| `tools/memory_crypt.py` | CLI: encrypt an existing memory file (verified copy, then swap) |
| `tests/memory/test_encryption.py` | Encrypted stores: on-disk secrecy, tamper/swap detection, passphrase rules, migration |
| `companion/app.py`, `companion/__main__.py` | Composition root: `AppConfig` (validated YAML), `Companion` (wires all parts), `python -m companion` |
| `config/companion.example.yaml`, `docs/running.md` | Example config and run guide |
| `tests/integration/test_app.py` | Whole companion from config over real TLS with a simulated robot |
| `tools/memory_review.py` | CLI: list / confirm / delete / clear / export memories; vault shows a count only and can be wiped |
| `tests/memory/test_review_tool.py` | Review tool: pending flow, --yes guards, vault contents never printed |
| `tests/companion/test_paced_speech.py` | Pause markers: trusted-only, caps, real silence through the pipeline, barge-in |
| `protocol/vectors_lib.py`, `protocol/vectors/*.json`, `tools/gen_vectors.py` | Firmware conformance vectors: recorded device conversations to replay on real firmware |
| `tests/protocol/test_conformance_vectors.py` | Vectors are current and replay exactly; `docs/firmware-conformance.md` |
| `companion/textnorm.py` | Normalizes curly quotes, zero-width chars and spaced letters before safety patterns run |
| `tests/test_review_fixes.py` | Regressions for defects found by the adversarial review |
| `companion/security/rotation.py` | `rotate_device_key` (resume-safe) |
| `tools/provision.py` | CLI: create (bundle) / list / revoke |
| `companion/application/ws_transport.py` | `WebSocketHostTransport` (wss server in a thread, `DeviceTransport`), `mac_gate`, ssl context helpers, `TokenBucket` |
| `companion/application/clock.py` | `SystemClock` |
| `simulator/ws_link.py` | `DeviceLink`: simulated device dials host over wss, reconnect/backoff, offline ticking, sleep closes socket |
| `tools/gen_certs.py` | Private CA + server cert generator (ECDSA P-256), cross-platform |
| `tests/psychology/*` | Library/sources, safety screens + validator, runner, engine flows, integration with chat/voice/director |
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

- `assets/sprites/{egg,baby}.json` + `source_egg|source_baby/`: stage sprite sets; `tools/desk_view.py` `ViewServer.sprite_doc(stage)` / `STAGE_FILES` picks one. `docs/your-checklist.md`: mic test and board arrival steps.
- `companion/speech/denoise.py`: `Denoiser` (streaming spectral gating), `DenoisedMic` wrapper used by `tools/desk_robot.py`.
- `tools/deploy.py`: `build()` creates a service folder (tls, keys.json, device bundle, companion.yaml, ninetails.service).
- `companion/personality/adaptation.py`: `PersonalityAdapter.observe(kind_counts)` bounded drift from activity counts; spoken controls in `learning/memory_talk.py`; wired in `companion/app.py` (`self.adapter`).
- `power.woke.sleep_reason` (optional): set in `simulator/device.py::_wake`, read in `companion/application/proactive.py`; vector `protocol/vectors/sleep_and_wake.json`.
- `companion/discoveries/{library,service}.py`: validated content library + `DailyInsights` (delivery, preferences, scripted follow-ups); `content/discoveries/*.yaml`; `tools/insights.py`; hooked into `ConversationEngine(insights=)` and `ProactiveDirector._deliver_insight`.
- `docs/test-coverage.md`: spec 39 required-test map; `tests/companion/test_provider_swap.py`.
