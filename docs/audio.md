# Audio transport

Control (signed JSON): `audio.start_capture {capture_id, stream_no, max_seconds}`, `audio.stop_capture`, `audio.play {stream_id, stream_no, format, style?, volume?}`, `audio.stop_playback`; events `audio.capture.started/stopped`, `audio.playback.finished {result: completed|interrupted|error, bytes_played, underruns}`. `audio.capture.chunk` stays in the schema but is not emitted (data travels as binary frames).
Data (WebSocket binary messages): 12-byte header + payload, defined in `protocol/audio.py`. Format: PCM16, 16 kHz, mono, little-endian, 1920-byte (60 ms) chunks, payload <= 4096. Opus is reserved in the header and rejected by firmware for now.

## Rules (all enforced in the simulator and tested)
- Frames have no per-frame MAC. They are accepted only on the TLS session that passed the signed first-frame gate, and only for the single stream the host opened with a signed control command. Rationale: TLS already protects integrity; per-chunk HMAC at 16 per second would cost power and bytes for no extra defense against an on-path attacker who cannot break TLS. A device must refuse binary audio on any non-TLS channel (`secure=False` is dropped).
- Strict parse: version, kind, stream_no != 0, reserved flag bits, codec, length == header length, even PCM length. Anything else is dropped and counted (`audio_rejected` on the device, `audio_dropped` on the host). Frames for unknown, wrong-direction or already-ended streams are dropped.
- `seq` starts at 0 and must increase by 1. A gap aborts playback (`invalid_state`); on capture the host marks the capture corrupt. (WebSocket is ordered and reliable, so a gap means a bug or an attack, not packet loss.)
- Capture: mic is opened only by a signed `start_capture` (and only if not muted and battery not low). Ends with an END frame, then `audio.capture.stopped` (reasons: host_request, timeout, low_battery, muted). Never exceeds `max_seconds` of data. Sleep, mute and low battery close it.
- Playback: `accepted` means the stream is open; `command.completed` + `audio.playback.finished` mean the audio has actually been played out. Device buffer ~2 s (64 KiB); overrun aborts with `busy`, a silent host aborts after 5 s with `timeout`, running dry is counted as an underrun and playback resumes after 150 ms of rebuffering. `audio.stop_playback` interrupts; sleep interrupts. One playback and one capture at a time.
- Host `DeviceClient.play_pcm()` sends a prebuffer (default 0.5 s, max 1.5 s) then paces at exactly real time. Faster than real time overruns the device buffer (found and fixed while testing).
- Host transport: audio byte-rate limit 96 KiB/s with 192 KiB burst (capture needs 32 KiB/s), queue bounded at 1 MiB if the app stops draining.
## Not covered
Real I2S capture/playback, codec/amp behavior, clock drift between host and a real sample clock, echo/feedback (speaker into mic), wake word, Opus, TTS/STT integration. Latency and glitch behavior on real Wi-Fi are unmeasured.
