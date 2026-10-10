# Hardware (board not yet received)

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
