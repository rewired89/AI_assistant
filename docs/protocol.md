# Protocol v1

Schemas: `protocol/schema/`. Examples: `protocol/examples/`. Rules: `protocol/compatibility.md`.
Envelope: `protocol_version, message_id, type, payload, auth{nonce, ts, mac}`. Max 16 KiB.
Auth: `mac = HMAC-SHA256(secret, canonical JSON of message with auth.mac removed)`; ts within 30 s; nonce single-use.

Commands (host->device): animation.play/stop, display.set_expression/set_brightness, audio.play/start_capture/stop_capture, power.sleep/wake, device.get_status/set_config.
Events (device->host): device.ready, button.pressed, touch.detected, battery.updated, audio.capture.started/chunk/stopped, animation.finished, power.sleeping/waking/woke, command.accepted/completed/failed.

Lifecycle: `command.accepted` means validated, not done. `command.completed` or `command.failed` ends it. Long actions (animation) complete later via their own event. Retrying the same `message_id` with a fresh nonce replays the cached ack/result and never re-executes. Sleeping devices receive nothing.
Not yet implemented: WebSocket/TLS transport, binary audio frames, reconnect loop (only `backoff_delays`), secret provisioning.
