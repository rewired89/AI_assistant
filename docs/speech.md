# Speech pipeline

Flow: device button (single press) -> `audio.start_capture` -> PCM frames -> end-of-speech (energy VAD) or second press -> STT -> onboarding answer *or* responder -> sentence split -> TTS (next sentence synthesized while the current one plays) -> `audio.play`. Host loop calls `VoicePipeline.poll()`; STT, reply and TTS run on worker threads, so a press can always cancel (TRANSCRIBING/THINKING) or interrupt (SPEAKING, which stops the speaker).
Animations follow the behavior engine (listening -> thinking -> talking -> idle). Looping animations outrank idle, so the pipeline sends `animation.stop` before returning to idle (a missing stop left the device stuck in "talking" during testing).

## Rules (tested)
- Microphone opens only on a single press of the configured button; double/long presses never start listening. The device can refuse (muted, low battery): the pipeline then speaks a scripted line and never tries to change the device's mic setting.
- Silence, clicks and empty transcripts never reach STT/LLM/memory. Transcripts are cleaned (control characters, length) because they are untrusted text.
- Audio is dropped as soon as STT has it; no transcript or audio is kept or logged here (`log` holds stage names only). Whatever consumes transcripts (responder, memory) owns its own retention rules.
- STT/LLM/TTS failures and timeouts are spoken from scripted lines (`species/*/speech.yaml`), never from model output or exception text. Bad TTS audio (empty, odd length, > 30 s) is rejected. Style is checked against the protocol's voice styles.
- Device sleeping or unresponsive: the pipeline stands down to IDLE instead of talking into the void. Corrupt capture (sequence gap) is not transcribed.
- Host-initiated speech (`speak()`, `offer_onboarding()`) never opens the microphone.
## Engines (none integrated yet; checked 2026-10 via secondary sources, verify before relying)
STT: Whisper family runs on CPU. faster-whisper (CTranslate2, int8) and whisper.cpp are the usual local options; small model reported around real-time or better on a modern CPU, but Whisper is not streaming, which is fine for push-to-talk utterances. Needs benchmarking on the MiniForum. TTS: Piper is the common offline pick, but its active repo (OHF-Voice/piper1-gpl) is GPL and voices carry their own licenses: fine for a personal gift, a problem for a closed commercial product. Keep engines behind `SpeechRecognizer` / `SpeechSynthesizer`; no cloud service is required or assumed.
## Limits
VAD threshold (500 RMS) and 1.0 s trailing silence are guesses until tested on the real microphone; no echo cancellation (the speaker can leak into the mic if the user presses during speech), no wake word, no streaming partial transcripts, STT/TTS threads cannot be killed (a hung engine is abandoned, not stopped). Fakes produce tones and scripted text, so audio quality and recognition accuracy are untested.
