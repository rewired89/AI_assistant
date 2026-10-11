# Firmware updates (OTA)

Status: implemented and tested against the simulator and over real loopback TLS. Nothing here has run on hardware.

## Flow
1. Offline, on the owner's machine: `python -m tools.ota build firmware.bin --key secrets/ota-signing.key --version X --build N --board <id>`. Output is `.bin` plus `.manifest.json` (product, board, version, build, size, sha256, created, sign_kid, optional requires_build) and an ECDSA P-256 / SHA-256 signature over the canonical manifest.
2. Owner starts `OtaUpdater.push()` (admin code or CLI only). The host re-checks the package itself, sends `ota.begin`, then streams 4096-byte raw frames (binary kind 3, codec raw, strict seq, END on the last short or empty frame) with a 16-chunk window; the device reports `ota.progress` every 4 chunks.
3. Device verifies: signature at begin (before touching flash), size and SHA-256 at the end -> `ota.verified`.
4. `apply()` (idle, battery >= 30%) switches the boot slot and reboots into a trial. The running slot is never written during a transfer.
5. Host health-checks (`device.get_status`: expected build, state `trial`) then sends `ota.confirm` -> `ota.committed`. Committing raises the anti-rollback floor.
6. No confirm within `confirm_timeout_s` (300 s), a failed self-test, or repeated boot failures: the device falls back to the last good slot by itself and reports `ota.rolled_back`.

## Rules
- Over TLS only. Unauthenticated frames are dropped as always.
- Signature protects the image; HMAC/TLS protect the channel. A stolen session or HMAC key cannot install firmware.
- Not reachable from conversation, voice, or any LLM tool. No automatic download: the host pushes, the device never pulls.
- Refused: untrusted key, other product/board, build not newer, `requires_build` unmet, too big, trial pending, power state not AWAKE/IDLE, battery < 40% at begin, audio active.
- Transfer aborts (`ota.failed`) on bad seq, wrong size, hash mismatch, low battery (<30%), power state change. Interrupted transfers resume from the last persisted progress mark (4 chunks) with the same `update_id` and manifest.
- Audio capture and playback are refused while receiving.

## Not verified (needs the real board)
Flash size and partition table (slot size 1.5 MiB is a placeholder), board id, ESP-IDF OTA/rollback API behavior, secure boot v2 with P-256, where the public key lives (burned vs flash), power-loss timing during flash writes, whether the self-test marker model matches real crashes. `NT-FAIL-SELFTEST` and `NT-BOOTLOOP` are simulator-only markers. Key rotation of the signing key (`sign_kid`) is supported in the format but has no tooling. Losing the signing key means re-flashing by cable.
