# Running the companion (host)

```powershell
Copy-Item config/companion.example.yaml config/companion.yaml   # then edit it
python -m companion --config config/companion.yaml
```
`companion/app.py` builds everything from that one file and runs the loop (poll voice pipeline, tick proactive director, offer onboarding, sync pending device settings). Ctrl+C stops it and saves state.

## Where things run
The language model runs on the Minisforum (Ollama there), not on a laptop: a 3B model froze a 5.7 GB laptop. The companion only sends text to `llm.url`, which must be a home-network address (private IP). On the Minisforum, Ollama must listen on the network: set `OLLAMA_HOST=0.0.0.0` for its service and allow port 11434 from the home network only. Ollama has no login, so anyone on that network can use it; keep the router firewall closed to the internet. Check the link from any machine with `python -m tools.check_llm --config config/companion.yaml`.

## Try it by voice before the hardware exists: the desk robot
`tools/desk_robot.py` wires the SIMULATED robot to your computer's microphone (`arecord`) and speaker (`aplay`) and runs the whole companion in one process. It is a test rig, not hardware: no screen, battery or real power behavior. Use headphones, or the speaker leaks into the mic.
```powershell
sudo apt install -y alsa-utils espeak-ng
python -m tools.desk_robot init --dir dev --llm-url http://192.168.1.42:11434 --llm-model llama3.2:3b --stt-model models/whisper-base.en
python -m tools.desk_robot run --dir dev
```
She speaks first (the onboarding questions). Open the printed address (http://localhost:8765) to see her face react, the sentence she is saying, her power/battery/mic state, and buttons (Talk, Mute, Wake, battery slider). The terminal also prints state names ("[listening] listen_end:silence") so a silent robot can be diagnosed; it never prints what was said. For the natural voice add `--tts-model models/kokoro --voice af_heart` to `init` after `python -m tools.fetch_models kokoro` and `pip install -e ".[tts]"`. ENTER = the button (listen, talk, stop talking). `m` + ENTER toggles the mic mute, `q` quits. `dev/` holds keys and her conversations' data, is git-ignored, and must not be shared. Tested with fake audio devices and fake engines only; a real mic and speaker run is the point of trying it.

## What is wired
Secret store + TLS wss server (device dials in) -> device client -> onboarding (answers change the live personality) -> safety gate -> psychology engine (private vault) -> conversation engine -> voice pipeline (STT/TTS) -> proactive director. Memory and vault open encrypted when `memory.encrypt: true`.

## Rules kept
- Unconfigured engines (`none`) are not faked: the robot fails visibly (scripted "I can't hear/think right now" lines) instead of pretending.
- Config is validated before anything starts; unknown keys are errors; a failed startup closes what it opened.
- OTA is not started by the app. It is an owner action (`OtaUpdater`), never reachable from voice or the LLM.
- The LLM is local-only unless `llm.allow_remote: true`. Private reflections use a model only if it is local.

## Not verified
Only tested against the simulated robot over loopback TLS with fake STT/LLM and fake TTS. Never run with real Whisper, a real LLM, or hardware. Windows service/auto-start is not set up. Personality drift exists in `Personality.adapt` but nothing calls it yet, so there is nothing to persist. Cubone has no `growth.yaml` yet and shares Ninetales' growth rules.
