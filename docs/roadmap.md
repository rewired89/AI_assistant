# Roadmap

- [x] Phase 0 architecture docs, repo, protocol schema (sim-level)
- [x] Phase 1 skeleton: config validation, tests, interfaces
- [~] Phase 4 protocol: codec, auth, simulated device, client done; wss transport + reconnect done (sim, loopback); binary audio frames done (sim); secret provisioning + rotation done (sim)
- [~] Phase 5 power: state machine + persistence in simulator only; real hardware and measurements pending
- [ ] Phase 2 minimal physical device (blocked on board)
- [ ] Phase 3 sprite engine
- [~] Phase 6: tamagotchi state model + behavior engine done (tested); onboarding done (tested, scripted); habit recognition pending
- [~] Phase 7 memory: store, vault, retention, extraction rules done; LLM extractor, review UI pending
- [~] Phase 8 voice: pipeline + fakes done and tested (sim, loopback TLS); LLM responder + proactive speech done against fakes + stub server; real STT/TTS/LLM runs, wake word pending
- [ ] Phase 6 rest, 7 memory, 8 AI + voice, 9 psychology, 10 personality tuning, 11 art, 12 Cubone on same core

Next concrete steps: (1) board arrives: fill `hardware.md`, dump flash; (2) WebSocket+TLS transport with the same tests; (3) tamagotchi state model with bounded decay and persistence tests; (4) memory store with consent and deletion.
