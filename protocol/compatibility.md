# Protocol compatibility

- `protocol_version` is an integer in every envelope. Current: **1**.
- Additive, backward-compatible changes (new optional payload fields) keep the version. Anything that removes or changes meaning bumps it.
- Payload schemas use `additionalProperties: false`, so unknown fields are rejected, not ignored. A new field therefore needs a version bump or a negotiated capability until both sides ship it.
- `device.ready.protocol_versions` lists what the firmware speaks. The host must pick a common version or refuse to command the device.
- A device receiving an authenticated message with an unsupported version replies `command.failed` and does nothing else.
- Check order on the device: size, JSON, envelope, **auth**, version, duplicate, payload, state. Unauthenticated input never gets a reply.
