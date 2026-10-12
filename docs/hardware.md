# Hardware (board not yet received)

## Candidate board: ESP32-S3-Touch-LCD-1.54 (VENDOR CLAIMS, UNVERIFIED)
Taken from the listing text. Nothing here is measured. No GPIOs, battery capacity or current draw are known.

| Claim | Value (vendor) |
|---|---|
| SoC | ESP32-S3R8, dual-core LX7 240 MHz, 512 KB SRAM, 8 MB PSRAM (in package), 16 MB flash |
| Display | 1.54" LCD, 240x240, 262K colour, capacitive touch (controller names UNKNOWN) |
| Audio in | ES7210 dual-mic ADC, echo cancellation claimed |
| Audio out | ES8311 codec + NS4150B amp + speaker |
| Motion | QMI8658 IMU |
| Input | 3 buttons (which is mute/PTT/power UNKNOWN) |
| Power | MX1.25 LiPo header, charge/discharge circuit (chip, limits, capacity UNKNOWN); USB-C |
| Other | TF slot, Wi-Fi 2.4 GHz, BLE 5, factory firmware with AI chat |

Needed before firmware: vendor wiki/schematic (pin table, display and touch controller, charger/fuel gauge, I2C addresses), photos, `esptool flash_id`, flash dump.
Sprite impact: a 240x240 square screen is the editor's default preview; 48x48 art scales 5x exactly. Still verify on the device.


Everything below is UNVERIFIED. Do not write board code from guesses. Fill this in with evidence (photos, schematic, `esptool chip_id`/`flash_id`, vendor docs) when the board arrives.

| Item | Value | Evidence |
|---|---|---|
| Vendor / model | UNKNOWN | |
| ESP32-S3 variant, flash, PSRAM | UNKNOWN | `esptool flash_id` |
| Display controller, size, interface | UNKNOWN | |
| Microphone (PDM/I2S), codec/amp | UNKNOWN | |
| Speaker | UNKNOWN | |
| Buttons / touch | UNKNOWN | |
| Battery chemistry/capacity | UNKNOWN | |
| Charger / PMIC / fuel gauge / ADC divider | UNKNOWN | |
| RTC or external wake source | UNKNOWN | ESP32 internal RTC timer is available on the chip, but that is a chip fact, not proof the board wiring allows it |
| GPIO map | UNKNOWN | |
| Bootloader replaceable | UNKNOWN | |

## Arrival checklist
1. Photograph both sides, read chip markings. 2. Check vendor page for firmware name (xiaozhi-esp32?) and repo. 3. Plug in USB-C, `esptool.py chip_id`, `flash_id`; **dump original flash before anything else** (`read_flash`). 4. Identify codec/display from firmware boot logs. 5. Measure battery voltage and current with a USB power meter. 6. Record results above.

## Abstraction
All pins live in `firmware/hardware/board_<name>.*`. HAL interfaces: display, sprite renderer, input, audio capture/playback, battery, power, network, storage, clock/wake. Not written yet.
