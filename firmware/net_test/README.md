# Network test (fourth firmware of ours)
Joins your Wi-Fi, syncs the clock, opens wss:// to the companion host and verifies its certificate against our private CA.
1. On the host (the Minisforum): `python -m companion --config deploy/companion.yaml` must be running.
2. Copy the CA (public, not secret) next to the code: `cp deploy/tls/ca.pem firmware/net_test/main/ca.pem` (git-ignored).
3. In bash with ESP-IDF active: `cd firmware/net_test && idf.py set-target esp32s3 && idf.py menuconfig`
   Go to "Nine Tails network test" and enter your 2.4 GHz Wi-Fi name and password (kept in the git-ignored sdkconfig).
4. `idf.py build && idf.py -p /dev/ttyACM0 flash monitor`
Expected: `Wi-Fi up`, `clock synced`, `wss CONNECTED`, then the host closes with 4001/4003 because this test sends no signed message yet.
Not compiled or run by the author when written.

## Signed hello (second part)
After the TLS connection the firmware sends a signed `device.ready`, so the host accepts it instead of closing with 4001.
Put the device id and key into the local, git-ignored sdkconfig WITHOUT typing them (run once `idf.py set-target esp32s3` first):
```
python -m tools.fw_config --bundle deploy/ninetails-01.bundle.json --project firmware/net_test
```
The key is a secret. Never paste it in chat or commit it. Delete the bundle file after you have flashed.

## If the board seems dead (no USB port)
A crash loop can reboot faster than the PC can enumerate USB. Recover: unplug, hold BOOT/- (right button), plug in, hold 3 s, release. `lsusb | grep 303a` must show a device. Then flash anything known-good, or restore the factory image: `esptool --port /dev/ttyACM0 write-flash 0 factory_backup.bin`. App firmware cannot brick the ROM bootloader.
The panic mode in sdkconfig.defaults is HALT (print the crash and stop) so a crash is readable instead of looping. An existing local sdkconfig keeps its old value: delete the `CONFIG_ESP_SYSTEM_PANIC_PRINT_REBOOT` line and append `CONFIG_ESP_SYSTEM_PANIC_PRINT_HALT=y`.
