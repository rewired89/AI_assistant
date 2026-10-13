# Voice test (device firmware v0.3)
The host plays speech through the board's speaker (`audio.play` + binary frames) and can record the microphone (`audio.start_capture`).
- The microphone is MUTED at every boot. Only the physical **+/KEY** button (GPIO4) toggles it. The host can never unmute (protocol rule).
- Everything else the host sends is answered `command.failed unsupported_command` (animations, display, status) until the screen firmware exists.
Setup (same as net_test, in bash with ESP-IDF active; the venv terminal for the Python step):
```
cp deploy/tls/ca.pem firmware/voice_test/main/ca.pem
cd firmware/voice_test && idf.py set-target esp32s3 && idf.py menuconfig      # Wi-Fi name and password under "Nine Tails network test"
python -m tools.fw_config --bundle deploy/ninetails-01.bundle.json --project firmware/voice_test
idf.py build && idf.py -p /dev/ttyACM0 flash monitor
```
Not compiled or run by the author when written.
