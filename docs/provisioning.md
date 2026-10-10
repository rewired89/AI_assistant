# Secret provisioning and rotation

## Model
Each robot has its own random 256-bit HMAC key (plus a pinned copy of the public CA and the host URL). The host holds all keys in `secrets/keys.json`. TLS gives confidentiality and host identity; the per-device key gives device identity. A stolen robot exposes only its own key and can be revoked.

## First-time setup (physical, one robot at a time)
1. `python -m tools.gen_certs --host <name-or-ip>` (once) makes the CA and server certificate in `secrets/tls/`.
2. `python -m tools.provision create robot-01 --url wss://<host>:8443 --ca secrets/tls/ca.pem` writes `secrets/robot-01.bundle.json` (0600): device id, key, CA, URL.
3. Put the bundle on the robot over USB at flash time (**hardware step, not built or verified: NVS layout, flash encryption and secure boot depend on the real board**). Then delete the bundle file.
4. The robot refuses to be provisioned twice (`provision()` raises). Replacing keys needs a deliberate `factory_reset()` (physical action on the real device) and a new bundle (`revoke` then `create`).
5. First valid frame marks `provisioned_at` in the host store, so `list` shows whether delivery worked.
Why not pair over the network with a code? Without a PAKE library that needs hand-rolled crypto and an unauthenticated first contact; USB avoids both. Revisit if a vetted PAKE exists for the ESP32.

## Rotation
`rotate_device_key()` (host, over the live wss session): mint kid N+1, persist it, send `security.rotate_key` signed by the old key. The device (TLS only, kid must be exactly next, no rotation in flight) stores the new key, signs with it, but still accepts the old one. Any message verified under the new key makes the verifier adopt it and retire older keys, on both sides. If the host crashes before sending, or the completion is lost, calling it again resumes with the same key; the device's next reply under the new key resolves the host. Tested.
Rotate after any suspected exposure and periodically (no schedule is automated yet).

## Storage and handling
Host file is created 0600 and refused if group/other readable (POSIX). Windows has no such check: use the user-profile ACL and/or set `NINETAILS_KEY_PASSPHRASE` (scrypt + Fernet) so the file is encrypted at rest, at the cost of supplying the passphrase when the host starts. Bundles contain a live key; they are git-ignored (`*.bundle.json`, `secrets/`). Keys never appear in `repr`, `list` output, status results or errors. Back up `keys.json` (or you must re-provision every robot).
## Open
On-device key protection is unverified; the CA signing key should live offline after the server cert exists; no automatic rotation schedule; single host key store (no HSM/keyring integration).
