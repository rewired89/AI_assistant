# Roadmap

- [x] Phase 0 architecture docs, repo, protocol schema (sim-level)
- [x] Phase 1 skeleton: config validation, tests, interfaces
- [~] Phase 4 protocol: codec, auth, simulated device, client done; wss transport + reconnect done (sim, loopback); binary audio frames done (sim); secret provisioning + rotation done (sim); OTA (signed A/B, trial, auto rollback) done (sim)
- [~] Phase 5 power: state machine + persistence in simulator only; real hardware and measurements pending
- [ ] Phase 2 minimal physical device (blocked on board)
- [~] Phase 3 sprite engine: sprite format + browser pixel editor + preview done (desk rig only); device renderer and screen specs need the board
- [~] Phase 6: tamagotchi state model + behavior engine done (tested); onboarding done (tested, scripted); routine recognition + spoken memory control done (tested)
- [~] Phase 7 memory: store, vault, retention, extraction rules done; LLM extractor and a friendly review UI pending (CLI review tool exists)
- [~] Phase 8 voice: pipeline + fakes done and tested (sim, loopback TLS); LLM responder + proactive speech done against fakes + stub server; real STT/TTS/LLM runs, wake word pending
- [~] Phase 9 psychology engine: implemented + synthetic-scenario tests; NEEDS clinician review before real use
- [~] Part XVII Daily Psychology Discoveries: engine, validation, delivery, voice controls done (sim); 6 entries verified at abstract level, needs owner spot-check and more entries
- [~] Productivity (voice notes, dictation, lists, phone app): notes + lists + phone app + journal tab done (sim/HTTP); reminders done (sim/HTTP, `docs/reminders.md`); audio notes, OS notifications next
- [ ] Phase 6 rest, 7 memory, 8 AI + voice, 9 psychology, 10 personality tuning, 11 art, 12 Cubone on same core

Next concrete steps: (1) board arrives: fill `hardware.md`, dump flash; (2) WebSocket+TLS transport with the same tests; (3) tamagotchi state model with bounded decay and persistence tests; (4) memory store with consent and deletion.

## After the board bring-up (next steps, in priority order)
Status: buttons, screen, touch, sprites, sleep, speaker, mic, voice loop all ran on the real board with the owner. Still a desk rig, not a gift.
1. **Where does the host live?** DECIDED (owner, direction not final): on HER MacBook Air (M1, RAM unverified, maybe 16 GB) on her home Wi-Fi, so nothing is exposed to the internet and the robot reaches it over the LAN. Her data stays on her machine under her passphrase; the owner keeps maintenance through signed update packs she approves, NOT remote access to her data. Open risks: Mac sleeps/lid closed = robot offline; host code untested on macOS/Apple Silicon; needs a one-click installer (she has no terminal); Wi-Fi discovery by name (mDNS) instead of a fixed IP; model speed and memory unmeasured on her Mac. First step: one test session on her Mac (check_llm timing, Whisper, Kokoro). Original options were: The robot dials the host over Wi-Fi. If she lives elsewhere, the robot cannot reach the Minisforum without a decision: (a) router port-forward + dynamic DNS (breaks under CGNAT), (b) a VPN (WireGuard on the board, or a relay), or (c) a small host at her home. Blocks everything below. OWNER DECISION NEEDED.
2. **Wi-Fi setup without a computer.** Wi-Fi name/password and device key are compiled in. She needs a way to join her own Wi-Fi (setup hotspot or phone page), and the key must not sit unencrypted in flash (NVS/flash encryption UNVERIFIED).
3. **Host as a service.** `tools.deploy init` writes a systemd unit that has never run on the real machine: auto-start, restart on crash, logs.
4. **Reliability.** ES7210 mic sometimes starts quiet or NACKs (restart rather than retry), reconnect after Wi-Fi loss, watchdog.
5. **Reply speed.** About 6 to 8 s now; target 3 s: speak the first sentence while the rest is written, smaller or quantized model, faster TTS.
6. **Power.** No battery detected. Until verified, USB power only; never drive the battery gate (GPIO2) low.
7. **Case/enclosure**, clinician review of psychology scripts, privacy review (her data lives on the owner's machine: consent and who can read it).
