# Firmware / host boundary

| Firmware owns | Host owns |
|---|---|
| Hardware, GPIO, audio devices | LLM, STT/TTS orchestration |
| Battery thresholds and emergency shutdown | Personality, mood, needs, growth |
| Power state machine, wake deadline, sleep entry | Natural-language time -> exact timestamp |
| Brightness caps, mic mute, capture limits | Memory, psychology engine |
| Animation playback, priority, interruption | Choosing which animation/mood to request |
| Command validation and authorization | Typed-action validation before sending |
| Local persistence and corruption recovery | User profile, long-term data |

Rules: host requests, firmware decides. The host never touches GPIO. An LLM mood or conversation can never block power saving. With the host offline the device keeps: scheduled wake, battery protection, local animations (idle/offline), buttons. All verified in simulator only (`tests/firmware`).
