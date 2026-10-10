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
`pytest` (Python 3.11+, deps: jsonschema, pyyaml). Windows/PowerShell for user-facing commands.

## Status
Done (simulated only): protocol v1 schemas/codec/HMAC auth, simulated device (power FSM, sleep/wake persistence, battery thresholds, corruption recovery, dedupe), host device client, sleep-time builder, personality schema/validation, tamagotchi state model (needs, mood, caps, staged growth, persistence; rules in `species/*/growth.yaml`), memory (SQLite store + separate consent-gated reflection vault, retention, dedup, pending AI inferences, rule-based extraction), behavior engine (stimulus -> Plan of animation + voice style + dialogue intents; proactive gating; no-guilt constraints).
Not started: firmware, memory encryption at rest, embeddings, dialogue text generation, speech, LLM, psychology engine, WebSocket/TLS transport, OTA.
