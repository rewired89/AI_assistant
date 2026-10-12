// Waveshare ESP32-S3-Touch-LCD-1.54. All pins live here and nowhere else.
// SOURCE: waveshareteam/ESP32-S3-Touch-LCD-1.54, examples/.../ESP-IDF-5.5.1/01_factory/components/esp_bsp (vendor demo source).
// STATUS: display, backlight, I2C, touch and the three buttons are CONFIRMED by our own runs (see docs/hardware.md). Everything else is from vendor source and unconfirmed. Charger polarity, battery gate meaning and the divider ratio are UNVERIFIED.
#pragma once

// Display ST7789 240x240 on SPI2, mode 3, 40 MHz in the vendor demo; colour inversion on; backlight PWM 5 kHz.
#define NT_LCD_MOSI 39
#define NT_LCD_SCLK 38
#define NT_LCD_CS 21
#define NT_LCD_DC 45
#define NT_LCD_RST 40
#define NT_LCD_BL 46

// I2C bus (touch CST816, codecs ES8311/ES7210, IMU QMI8658 at 0x6B)
#define NT_I2C_SDA 42
#define NT_I2C_SCL 41
#define NT_TOUCH_INT 48
#define NT_TOUCH_RST 47
#define NT_IMU_ADDR 0x6B

// Audio I2S (ES8311 speaker codec, ES7210 mic ADC), speaker amplifier enable
#define NT_I2S_MCLK 8
#define NT_I2S_BCLK 9
#define NT_I2S_LRCK 10
#define NT_I2S_DIN 11   // from mic ADC
#define NT_I2S_DOUT 12  // to speaker codec
#define NT_PA_CTRL 7

// microSD (SDMMC 4-bit)
#define NT_SD_CLK 16
#define NT_SD_CMD 15
#define NT_SD_D0 17
#define NT_SD_D1 18
#define NT_SD_D2 13
#define NT_SD_D3 14

// Buttons (active low in the vendor demo)
#define NT_BTN_BOOT 0   // BOOT/-
#define NT_BTN_PWR 5    // PWR
#define NT_BTN_PLUS 4   // +/KEY

// Battery: ADC pin (vendor multiplies the pin voltage by 3), gate pin the demo drives high at start and low to power off, charging flag (low = charging in the demo)
#define NT_BAT_ADC 1
#define NT_BAT_POWER 2
#define NT_CHARGING 3
