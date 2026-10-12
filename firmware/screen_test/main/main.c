// Screen test: backlight at a LOW brightness, the panel cycles teal, pink, white, black, one color per second.
// Uses only the display pins from firmware/hardware/board_waveshare_esp32s3_lcd154.h (taken from the vendor demo, unverified until this runs).
// Does not touch the battery gate, charger, audio, SD or radio.
#include <stdint.h>
#include "board_waveshare_esp32s3_lcd154.h"
#include "driver/gpio.h"
#include "driver/ledc.h"
#include "driver/spi_master.h"
#include "esp_heap_caps.h"
#include "esp_lcd_panel_io.h"
#include "esp_lcd_panel_ops.h"
#include "esp_lcd_panel_vendor.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

static const char *TAG = "nt_screen_test";
#define W 240
#define H 240
#define STRIP 20
#define BACKLIGHT_PERCENT 30  // firmware owns brightness limits; the host never asks for more than this here

static uint16_t c565(uint8_t r, uint8_t g, uint8_t b)
{
    uint16_t v = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3);
    return (uint16_t)((v >> 8) | (v << 8));  // the panel expects the high byte first
}

static void backlight_init(void)
{
    ledc_timer_config_t t = {.speed_mode = LEDC_LOW_SPEED_MODE, .duty_resolution = LEDC_TIMER_10_BIT, .timer_num = LEDC_TIMER_0, .freq_hz = 5000, .clk_cfg = LEDC_AUTO_CLK};
    ESP_ERROR_CHECK(ledc_timer_config(&t));
    ledc_channel_config_t c = {.gpio_num = NT_LCD_BL, .speed_mode = LEDC_LOW_SPEED_MODE, .channel = LEDC_CHANNEL_0, .timer_sel = LEDC_TIMER_0, .duty = 0, .hpoint = 0};
    ESP_ERROR_CHECK(ledc_channel_config(&c));
    ESP_ERROR_CHECK(ledc_set_duty(LEDC_LOW_SPEED_MODE, LEDC_CHANNEL_0, (BACKLIGHT_PERCENT * 1023) / 100));
    ESP_ERROR_CHECK(ledc_update_duty(LEDC_LOW_SPEED_MODE, LEDC_CHANNEL_0));
}

void app_main(void)
{
    spi_bus_config_t bus = {.sclk_io_num = NT_LCD_SCLK, .mosi_io_num = NT_LCD_MOSI, .miso_io_num = -1, .quadwp_io_num = -1, .quadhd_io_num = -1, .max_transfer_sz = W * STRIP * 2};
    ESP_ERROR_CHECK(spi_bus_initialize(SPI2_HOST, &bus, SPI_DMA_CH_AUTO));
    esp_lcd_panel_io_handle_t io = NULL;
    esp_lcd_panel_io_spi_config_t io_cfg = {.cs_gpio_num = NT_LCD_CS, .dc_gpio_num = NT_LCD_DC, .spi_mode = 3, .pclk_hz = 40 * 1000 * 1000, .trans_queue_depth = 10, .lcd_cmd_bits = 8, .lcd_param_bits = 8};
    ESP_ERROR_CHECK(esp_lcd_new_panel_io_spi((esp_lcd_spi_bus_handle_t)SPI2_HOST, &io_cfg, &io));
    esp_lcd_panel_handle_t panel = NULL;
    esp_lcd_panel_dev_config_t pc = {.reset_gpio_num = NT_LCD_RST, .rgb_ele_order = LCD_RGB_ELEMENT_ORDER_RGB, .bits_per_pixel = 16};
    ESP_ERROR_CHECK(esp_lcd_new_panel_st7789(io, &pc, &panel));
    ESP_ERROR_CHECK(esp_lcd_panel_reset(panel));
    ESP_ERROR_CHECK(esp_lcd_panel_init(panel));
    ESP_ERROR_CHECK(esp_lcd_panel_invert_color(panel, true));  // the vendor demo inverts; if colors look negative, flip this
    ESP_ERROR_CHECK(esp_lcd_panel_disp_on_off(panel, true));

    uint16_t *buf = heap_caps_malloc(W * STRIP * 2, MALLOC_CAP_DMA);
    if (!buf) {
        ESP_LOGE(TAG, "no DMA buffer");
        return;
    }
    backlight_init();
    // Color diagnostic: each color is held 2 s and named in the monitor. Compare what you SEE with the name.
    const struct { const char *n; uint8_t r, g, b; } cs[] = {
        {"RED 255,0,0", 255, 0, 0}, {"GREEN 0,255,0", 0, 255, 0}, {"BLUE 0,0,255", 0, 0, 255},
        {"DARK NAVY 10,24,32", 10, 24, 32}, {"DARK GRAY 60,70,78", 60, 70, 78}, {"MID GRAY 128,128,128", 128, 128, 128},
        {"TEAL 55,191,176", 55, 191, 176}, {"PINK 244,139,182", 244, 139, 182}, {"WHITE", 255, 255, 255}, {"BLACK", 0, 0, 0}};
    const int n = sizeof(cs) / sizeof(cs[0]);
    for (int i = 0;; i = (i + 1) % n) {
        uint16_t col = c565(cs[i].r, cs[i].g, cs[i].b);
        for (int p = 0; p < W * STRIP; p++) buf[p] = col;
        for (int y = 0; y < H; y += STRIP) ESP_ERROR_CHECK(esp_lcd_panel_draw_bitmap(panel, 0, y, W, y + STRIP, buf));
        ESP_LOGI(TAG, "screen should be: %s", cs[i].n);
        vTaskDelay(pdMS_TO_TICKS(2000));
    }
}
