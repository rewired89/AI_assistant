# Voice test (device firmware v0.3)
The host plays speech through the board's speaker (`audio.play` + binary frames) and can record the microphone (`audio.start_capture`).
- The microphone is MUTED at every boot. Only the physical **+/KEY** button (GPIO4) toggles it; the host can never unmute. **BOOT/-** (GPIO0) is push-to-talk (sent as button.pressed "main"). To make her speak on demand run the host with `python -m companion --config deploy/companion.yaml --console` and type `say hello there`.
- Everything else the host sends is answered `command.failed unsupported_command` (animations, display, status) until the screen firmware exists.
Setup (same as net_test, in bash with ESP-IDF active; the venv terminal for the Python step):
```
cp deploy/tls/ca.pem firmware/voice_test/main/ca.pem
cd firmware/voice_test && idf.py set-target esp32s3 && idf.py menuconfig      # Wi-Fi name and password under "Nine Tails network test"
python -m tools.fw_config --bundle deploy/ninetails-01.bundle.json --project firmware/voice_test
idf.py build && idf.py -p /dev/ttyACM0 flash monitor
```
Not compiled or run by the author when written.

## Sprites (her pixel art on the screen)
The 3 sprite sets (egg, baby, adult) are packed into one file and written to the `sprites` flash partition (0x310000, 1 MiB). The firmware itself does not contain them, so flashing new firmware does not erase them, and updating the art does not need a rebuild.
```powershell
python -m tools.sprite_bundle
```
That writes `build/sprites.bin` and prints the flash command, to run in the ESP-IDF bash terminal (`python -m esptool --chip esp32s3 -p /dev/ttyACM0 write_flash 0x310000 build/sprites.bin`). The host tells the device which set to show (egg, baby or adult) from her growth stage. Without the sprites in flash the built-in eyes face is used. Flashing the new partition table (`idf.py flash`) is needed once because `partitions.csv` changed. Screen pixel colors are UNVERIFIED on the real panel.

## Network setup from a phone (v0.11, written but NOT yet run on the board)
Wi-Fi name, Wi-Fi password and the host address/port are stored in flash (NVS, namespace `ntcfg`) and set from a phone. The values compiled in through `idf.py menuconfig` stay as the fallback, so an existing board keeps working.
- **Open setup:** hold BOTH side buttons (left and right) for 4 seconds, or wait: if Wi-Fi does not come up within 90 s of boot the setup opens by itself.
- The screen shows 8 big digits. On a phone join the Wi-Fi network `NineTails-Setup-XXXX` using those 8 digits as the password, then open `http://192.168.4.1`, fill in the Wi-Fi name, password, the address of the computer running her host and the port (8443), press Save. The board restarts and joins.
- PWR cancels setup; 10 minutes without saving also restarts the board.
- The buttons and touch now work even while the Wi-Fi is down (before, the board waited for Wi-Fi forever and ignored them).
- The host's certificate must be valid for the address typed here (`tools.deploy init --host-ip ... --hostname ...`).
