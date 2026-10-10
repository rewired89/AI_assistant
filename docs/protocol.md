# Protocol v1

Schemas: `protocol/schema/`. Examples: `protocol/examples/`. Rules: `protocol/compatibility.md`.
Envelope: `protocol_version, message_id, type, payload, auth{nonce, ts, mac}`. Max 16 KiB.
Auth: `mac = HMAC-SHA256(secret, canonical JSON of message with auth.mac removed)`; ts within 30 s; nonce single-use.

Commands (host->device): animation.play/stop, display.set_expression/set_brightness, audio.play/start_capture/stop_capture, power.sleep/wake, device.get_status/set_config.
Events (device->host): device.ready, button.pressed, touch.detected, battery.updated, audio.capture.started/chunk/stopped, animation.finished, power.sleeping/waking/woke, command.accepted/completed/failed.

Lifecycle: `command.accepted` means validated, not done. `command.completed` or `command.failed` ends it. Long actions (animation) complete later via their own event. Retrying the same `message_id` with a fresh nonce replays the cached ack/result and never re-executes. Sleeping devices receive nothing.
## Transport: wss
The device dials the host (`wss://host:port`), so no device IP is needed. First frame must be a valid signed message (the simulator sends `battery.updated`) within 10 s or the socket is closed (4001/4003). One device at a time; only an authenticated newcomer replaces the current connection (close 4000). Text frames only, <= 16 KiB (binary closes 1003, oversize 1009), token-bucket rate limit (close 1008), 20 s keepalive pings. A sleeping device closes the socket and stays off the network until it wakes. Messages sent with no connection raise `TransportClosed`; events produced while offline are not buffered.
Envelope has optional `device_id` (signed; a device-bound verifier requires it) and `auth.kid` (key id, default 1). `security.rotate_key {kid, key}` is accepted only on a TLS channel. Not yet implemented: binary audio frames, device-side (C++) client, plain-HTTP OTA path.
