# Security

Implemented (tested in sim): per-message HMAC, timestamp window, nonce replay cache, size limit, schema validation, silent drop of unauthenticated input, min 16-byte secret, mute-only mic config, battery-based safety caps, checksummed state.
TLS (host wss server, TLS >= 1.2, private CA pinned by the client, hostname check, tested on loopback) is implemented; `tools/gen_certs.py` makes the CA/server cert. Not tested: cert expiry/rotation handling, ESP32 mbedtls compatibility (needs hardware), device flash holding the pinned CA.
Not implemented: secret provisioning/rotation, secure storage of the secret on-device (ESP32 flash encryption / NVS encryption to evaluate), OTA signing, rate limiting, secure boot.
Rules: no secrets in repo (`.gitignore` covers `.env`, `*.key`, `secrets/`); secret comes from environment/config at runtime; LLM output is never executed, only parsed into typed actions.
Threat notes: shared symmetric key means a stolen device exposes the key; use per-device keys. Keep `secrets/` (CA key, server key, HMAC secret) off git; the CA key can sign certificates for any host, so store it offline once the server cert exists. Plain `ws://` is refused by design (no code path). The client never uses proxy env vars.
