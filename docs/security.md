# Security

Implemented (tested in sim): per-message HMAC, timestamp window, nonce replay cache, size limit, schema validation, silent drop of unauthenticated input, min 16-byte secret, mute-only mic config, battery-based safety caps, checksummed state.
Not implemented: TLS, secret provisioning/rotation, secure storage of the secret on-device (ESP32 flash encryption / NVS encryption to evaluate), OTA signing, rate limiting, secure boot.
Rules: no secrets in repo (`.gitignore` covers `.env`, `*.key`, `secrets/`); secret comes from environment/config at runtime; LLM output is never executed, only parsed into typed actions.
Threat notes: shared symmetric key means a stolen device exposes the key; use per-device keys. HMAC does not hide content, so TLS is required before real conversations cross a network.
