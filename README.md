# Nine Tails

Modular AI companion robot: ESP32-S3 firmware + replaceable AI host + configurable character.
Status: **Phase 0/1 + protocol/simulator slice.** No hardware has been touched; no firmware exists yet. See `docs/roadmap.md`.

## Run the tests
```powershell
python -m venv .venv; .\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
pytest
```

## Layout
`protocol/` wire format, schemas, auth | `simulator/` reference model of firmware logic |
`companion/` host (interfaces, device client, personality, scheduling) | `species/` character configs |
`firmware/` placeholder until the board is identified | `docs/` architecture, decisions, security, privacy, psychology design.

Read `CLAUDE.md` for working rules and `CODEMAP.md` for file-level map.
