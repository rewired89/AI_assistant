# Your checklist (PowerShell, from the repo folder with the venv active)

Activate the venv in every new terminal: `./.venv/bin/Activate.ps1`

## 1. Test the microphone (laptop)
```
git pull origin main
arecord -l
python -m tools.mic_check
```
Speak for 5 seconds. A moving bar means the mic works. The last line says the verdict (NO AUDIO, SILENT, or OK).
If it names the wrong device: `python -m tools.mic_check --device plughw:1,0` (use the card,device numbers from `arecord -l`).
Then run the robot: `python -m tools.desk_robot run` and send me the `You said` line, the mic bar behaviour and any `[...]` terminal lines.

## 2. Board arrives (do these in order, send me every output)
1. Photograph both sides. Do NOT flash anything yet.
2. Install the tool: `pip install esptool`
3. Plug in USB-C. Find the port: `ls /dev/ttyACM* /dev/ttyUSB*` (hold BOOT while plugging in if none appears).
4. `esptool --port /dev/ttyACM0 chip_id` then `esptool --port /dev/ttyACM0 flash_id`
5. BACK UP the factory firmware first (16 MB): `esptool --port /dev/ttyACM0 read_flash 0 0x1000000 factory_backup.bin`
   Keep the file safe (do not commit it). Only after this is it safe to flash anything.
6. Measure battery voltage and charging current with a USB power meter. Write the numbers down.
7. Find the vendor wiki/schematic page for "ESP32-S3-Touch-LCD-1.54", download the pin table, send it to me.

## 3. Minisforum (tomorrow)
Enable SSH on it (`sudo apt install openssh-server`), then use VS Code Remote-SSH to `192.168.1.42`.
