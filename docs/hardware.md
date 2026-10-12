# Hardware (board not yet received)

## CONFIRMED FROM PHOTOS (owner's unit, label on the back of the enclosure; printed text, not measured)
Waveshare ESP32-S3-Touch-LCD-1.54 in a white enclosure. Label: display ST7789 240x240, touch CST816, mic ADC ES7210 (AEC) with 2 mics, codec ES8311, speaker, QMI8658 6-axis IMU, 8 MB PSRAM, 16 MB flash, microSD slot, USB-C. Three side buttons labelled +/KEY, PWR, BOOT/-. Pin map, charger and battery: still UNVERIFIED (need the Waveshare wiki/schematic).

## MEASURED with esptool 5.5.0 on the owner's unit (USB-Serial/JTAG, 303a:1001)
| Item | Value |
|---|---|
| Chip | ESP32-S3 (QFN56), revision v0.2, dual core, 240 MHz, 40 MHz crystal |
| PSRAM | 8 MB embedded (AP_3v3) |
| Flash | 16 MB, quad, 3.3 V (manufacturer 0x20, device 0x4018) |
| Factory firmware | Wi-Fi setup mode: AP SSID ESP32S3_1_54, page at 192.168.4.1 (seen on screen) |
| Factory backup | `factory_backup.bin`, 16 MiB read in 60.5 s (kept by owner, never committed) |
| Battery | none connected on this unit (screen dies when USB is removed) |

## Pin map from the vendor demo source (public repo waveshareteam/ESP32-S3-Touch-LCD-1.54, ESP-IDF 5.5.1 `01_factory/components/esp_bsp`)
Recorded in `firmware/hardware/board_waveshare_esp32s3_lcd154.h`. These come from Waveshare's code, not from our own run, so they stay UNVERIFIED until a test of ours drives each one.
Display SPI2 (MOSI 39, SCLK 38, CS 21, DC 45, RST 40, BL 46); I2C SDA 42 / SCL 41; touch INT 48 / RST 47; I2S MCLK 8, BCLK 9, LRCK 10, DIN 11, DOUT 12; speaker amp enable 7; SD CLK 16, CMD 15, D0 17, D1 18, D2 13, D3 14;
buttons BOOT 0, PWR 5, PLUS 4; battery ADC 1 (x3), battery gate 2, charging 3. Unknown: charger chip, battery size, the gate pin's exact effect (the demo's long PWR press drives it low to power off), and I2C addresses other than the IMU (0x6B).

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
