# CLAUDE.md

Project: Nine Tails. Full spec lives with the owner; the rules below are the load-bearing ones.

## Rules
- COMMIT AND PUSH EVERYTHING TO MAIN, DO NOT CREATE NEW BRANCHES.
- Never invent GPIOs, battery capacity, current draw or peripheral capability. Unknowns go in `docs/hardware.md` as UNVERIFIED and behind an interface.
- Firmware owns hardware safety (power, brightness, mic). The host only *requests*. The LLM never sends raw device commands: typed actions -> host validation -> signed protocol message -> device validation.
- Accepted != done. Completion is a separate event.
- Unauthenticated input is dropped silently.
- Mic can be muted remotely but only unmuted physically.
- Psychological Growth Engine is separate from personality/tamagotchi, opt-in, private by default, no diagnosis, no scores, no shame/guilt/dependency. Nothing from her reflections reaches the owner.
- Conversation can never edit personality config or system prompt (`apply_update` accepts only `user_settings`).
- No Pokémon artwork or branding in anything commercial. Original assets only.
- Do not claim a feature works until a test or hardware run shows it. Simulator results are not hardware results.

## Commands
`pytest` (Python 3.11+, deps: jsonschema, pyyaml, websockets, cryptography; dev: pytest). Windows/PowerShell for user-facing commands.

## Status
Done (simulated only): protocol v1 schemas/codec/HMAC auth, simulated device (power FSM, sleep/wake persistence, battery thresholds, corruption recovery, dedupe), host device client, sleep-time builder, personality schema/validation, tamagotchi state model (needs, mood, caps, staged growth, persistence; rules in `species/*/growth.yaml`), memory (SQLite store + separate consent-gated reflection vault, retention, dedup, pending AI inferences, rule-based extraction), behavior engine (stimulus -> Plan of animation + voice style + dialogue intents; proactive gating; no-guilt constraints), onboarding (2 core questions, paced optional ones, scripted copy per species, config patch + memory prefs + device settings, YAML copy in `species/*/onboarding.yaml`), wss:// transport (device dials host; TLS>=1.2 with pinned private CA, pre-auth MAC gate, 16 KiB text frames, rate limit, replace-only-by-authenticated, simulator link with backoff/offline ticking; tested over real loopback TLS), secret provisioning (per-device keys + key ids, device_id binding, host `SecretStore` 0600/optional passphrase encryption, USB provisioning bundle via `tools/provision.py`, one-shot device provisioning + factory reset, TLS-only key rotation with crash-safe resume, revocation), binary audio frames (12-byte header, capture device->host, playback host->device, stream numbers from signed control commands, strict seq/length/codec checks, TLS-only, byte rate limit + bounded queue, paced playback with underrun/overrun reporting; PCM16 16 kHz mono only; sim + loopback TLS tests), speech pipeline (`companion/speech`: push-to-talk state machine, energy VAD, STT/TTS/responder as futures so button can cancel/barge-in, onboarding routed by voice, scripted device-state lines, no audio/transcript retention; only fake STT/TTS exist so far), LLM responder (`companion/conversation`: `ConversationEngine` behind the `LanguageModel` interface; prompt with data-block quoting + canary; output safety filter (guilt, diagnosis, human claims, prompt leak) with one retry; three validated tools only: remember/request_sleep/show_emotion; memory writes grounded in the user's words else pending confirmation; sleep only if the user asked, executed after speech via whitelist; local-only Ollama adapter; tested with fake models and a stub HTTP server, never a real LLM).
Not started: real STT/TTS engines, a real LLM run, intent-to-text (`compose_intent`) for proactive speech, firmware, memory encryption at rest, embeddings, psychology engine, opus/compression, real mic/speaker I/O, on-device key storage (NVS/flash encryption unverified), OTA.
