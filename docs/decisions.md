# Decisions

## D1 Host language: Python 3.11+
Why: fastest path to LLM/STT/TTS libraries, pytest, runs on the MiniForum. Alternatives: Node, Go. Limits: packaging on Windows. Replaceable: interfaces in `companion/interfaces.py`. No hardware, no internet needed.

## D2 Protocol: JSON envelope + JSON Schema, HMAC per message, TLS at transport
Why: schemas are language-neutral so C++ firmware can reuse them; HMAC gives auth independent of transport; replay window + nonce cache. Alternatives: protobuf (smaller, harder to debug), MQTT. Limits: shared symmetric secret (provisioning and rotation unsolved), JSON is heavy on ESP32 (ArduinoJson/cJSON fine). Transport (WebSocket/TLS) not yet implemented. Audio will use separate binary frames, not this envelope.

## D3 Firmware toolchain: OPEN
Candidates: ESP-IDF (likely, matches xiaozhi-esp32), PlatformIO/Arduino. Decide after board ID. Requires hardware.

## D4 Simulator as executable spec
`simulator/device.py` encodes the device-side rules (validation order, power FSM, sleep persistence). Firmware tests must replay the same scenarios. It does not prove hardware behavior.

## D5 Mic is mute-only from the host
Schema allows only `mic_enabled: false`. Unmuting needs a physical action. Privacy over convenience.

## D6 Silent drop for unauthenticated input
No reply means no oracle for probing. Cost: harder debugging; use device-side `rejected_count` and logs.

## D7 Deep sleep = device ignores commands
While SLEEPING the radio is off; `power.wake` from host cannot arrive. Wake is scheduled or physical only.
