# Firmware conformance vectors

`protocol/vectors/*.json` record scripted conversations with the simulated device: signed messages in, exact outputs out (auth drops, command lifecycle, retries, sleep/wake, battery protection, mic rules, audio streams). The real firmware must reproduce them.

## For the firmware
Load the file, feed each step (clock fixed at `start_time`, advanced by `advance` steps; `text` = raw signed JSON from the host; `binary` = hex frame; `secure` = arrived over verified TLS), and compare the outputs in order. A text output is compared on `type` and `payload` only; envelope ids, nonce, ts and MAC differ per run but must verify with the device key. `boot_expect` is what the device sends at power-on. Shared test key (published, never use for a real robot): `00112233...eeff`, device_id `vector-bot`.

## For the host/simulator
`python -m tools.gen_vectors` rewrites them, `--check` fails if stale (a test runs it). If you change simulator behavior on purpose, regenerate and review the JSON diff: that diff is the change to the firmware contract. Mutation-checked: removing the low-battery brightness cap makes the vector test fail.

## Limits
Time is simulated, so real timing (debounce, I2S jitter, flash writes) is not covered. The simulator is the spec only where the vectors say so; a vector passing on firmware does not prove hardware safety (battery cut-offs, current draw, mic wiring are UNVERIFIED, see `hardware.md`). Not covered yet: OTA, key rotation, provisioning, capture rate limits, config persistence across power loss. Add scenarios in `tools/gen_vectors.py`.
