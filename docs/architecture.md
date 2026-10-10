# Architecture

```
 Girlfriend <-> [ Robot firmware ] <== signed WebSocket/TLS ==> [ Companion host ] <-> LLM / STT / TTS / memory
                 hardware, power,                                 personality, needs, growth,
                 local safety, display                            memory, psychology engine
```

Host modules (all behind interfaces in `companion/interfaces.py`): conversation pipeline, personality, behavior, tamagotchi, memory, speech, scheduling, psychology (separate, opt-in).
Firmware modules: hardware (board config + HAL), display/animation, audio, inputs, power, storage, networking, protocol_client.

Pipeline (host): input -> STT -> intent/context -> memory retrieval -> personality/behavior -> psychology policy (only if enabled and relevant) -> LLM -> response validation -> typed actions -> memory candidates -> consent/policy decision.

## Validation state
| Area | State |
|---|---|
| Protocol, auth, device logic | Implemented and tested against the simulator only |
| Everything touching hardware | Not started, board not identified |
| Memory, tamagotchi, behavior, speech, LLM, psychology | Not started |

## Prior art (checked 2026-10)
- [xiaozhi-esp32](https://github.com/78/xiaozhi-esp32): MIT, 70+ boards, ESP-IDF, wake word, WebSocket/UDP, MCP. Official server is closed; community self-hostable servers exist (e.g. xinnan-tech/xiaozhi-esp32-server). Many cheap "AI voice robot" boards ship with it, so your board very likely runs it. Unverified until the board arrives.
- Espressif ESP-SparkBot and M5Stack StackChan: open-source ESP32-S3 desktop companions with faces.
- Recent hobby pet projects exist (ESP32 display pet with an Android LLM host). I found no open project combining a Tamagotchi growth model, long-term consented memory and a reflective-psychology module. That combination is the novel part; the hardware/voice plumbing is not.
- Implication: reuse xiaozhi-esp32's board support and audio drivers if licensing and fit allow (MIT), instead of rewriting drivers. Decide after the board is identified. See `decisions.md` D3.
