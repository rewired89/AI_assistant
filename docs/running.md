# Running the companion (host)

```powershell
copy config\companion.example.yaml config\companion.yaml   # then edit it
python -m companion --config config\companion.yaml
```
`companion/app.py` builds everything from that one file and runs the loop (poll voice pipeline, tick proactive director, offer onboarding, sync pending device settings). Ctrl+C stops it and saves state.

## What is wired
Secret store + TLS wss server (device dials in) -> device client -> onboarding (answers change the live personality) -> safety gate -> psychology engine (private vault) -> conversation engine -> voice pipeline (STT/TTS) -> proactive director. Memory and vault open encrypted when `memory.encrypt: true`.

## Rules kept
- Unconfigured engines (`none`) are not faked: the robot fails visibly (scripted "I can't hear/think right now" lines) instead of pretending.
- Config is validated before anything starts; unknown keys are errors; a failed startup closes what it opened.
- OTA is not started by the app. It is an owner action (`OtaUpdater`), never reachable from voice or the LLM.
- The LLM is local-only unless `llm.allow_remote: true`. Private reflections use a model only if it is local.

## Not verified
Only tested against the simulated robot over loopback TLS with fake STT/LLM and fake TTS. Never run with real Whisper, a real LLM, or hardware. Windows service/auto-start is not set up. Personality drift (small trait changes from experience) is not persisted across restarts. Cubone has no `growth.yaml` yet and shares Ninetales' growth rules.
