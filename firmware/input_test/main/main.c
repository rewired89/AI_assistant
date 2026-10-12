// Input test: three button boxes at the top light teal while pressed, a pink dot follows a finger on the touch screen.
// Also scans the I2C bus at boot and logs every address that answers (evidence for docs/hardware.md).
// Pins come from firmware/hardware/board_waveshare_esp32s3_lcd154.h (vendor source, unverified until this runs).
// Does not touch the battery gate (GPIO2), charger, audio output, SD or radio.
#include <stdbool.h>
#include <stdint.h>
#include "board_waveshare_esp32s3_lcd154.h"
#include "driver/gpio.h"
#include "driver/i2c_master.h"
#include "driver/ledc.h"
#include "driver/spi_master.h"
#include "esp_heap_caps.h"
#include "esp_lcd_panel_io.h"
#include "esp_lcd_panel_ops.h"
#include "esp_lcd_panel_vendor.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "freertos/task.h"

static const char *TAG = "nt_input_test";
#define W 240
#define H 240
#define STRIP 20
#define BACKLIGHT_PERCENT 30
#define TOUCH_ADDR 0x15  // CST816 (vendor demo uses the esp_lcd_touch_cst816s default address)

typedef struct {
    bool btn[3];  // BOOT, PWR, PLUS
    bool touch;
    int tx, ty;
} state_t;

static SemaphoreHandle_t s_done;
static uint16_t C_BG, C_OFF, C_ON, C_DOT;

static uint16_t c565(uint8_t r, uint8_t g, uint8_t b)
{
    uint16_t v = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3);
    return (uint16_t)((v >> 8) | (v << 8));
}

static bool on_color_done(esp_lcd_panel_io_handle_t io, esp_lcd_panel_io_event_data_t *e, void *ctx)
{
    BaseType_t woken = pdFALSE;
    xSemaphoreGiveFromISR(s_done, &woken);
    return woken == pdTRUE;
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

static uint16_t pixel(int x, int y, const state_t *s)
{
    if (y >= 20 && y < 80)
        for (int i = 0; i < 3; i++) {
            int bx = 20 + i * 70;
            if (x >= bx && x < bx + 60) return s->btn[i] ? C_ON : C_OFF;
        }
    if (s->touch) {
        int dx = x - s->tx, dy = y - s->ty;
        if (dx * dx + dy * dy <= 144) return C_DOT;
    }
    return C_BG;
}

static void render(esp_lcd_panel_handle_t panel, uint16_t *buf, const state_t *s)
{
    for (int y0 = 0; y0 < H; y0 += STRIP) {
        for (int y = 0; y < STRIP; y++)
            for (int x = 0; x < W; x++) buf[y * W + x] = pixel(x, y0 + y, s);
        ESP_ERROR_CHECK(esp_lcd_panel_draw_bitmap(panel, 0, y0, W, y0 + STRIP, buf));
        xSemaphoreTake(s_done, pdMS_TO_TICKS(200));  // the buffer is reused, so wait until the panel has taken it
    }
}

void app_main(void)
{
    s_done = xSemaphoreCreateBinary();
    C_BG = c565(10, 24, 32);
    C_OFF = c565(60, 70, 78);
    C_ON = c565(55, 191, 176);
    C_DOT = c565(244, 139, 182);

    spi_bus_config_t bus = {.sclk_io_num = NT_LCD_SCLK, .mosi_io_num = NT_LCD_MOSI, .miso_io_num = -1, .quadwp_io_num = -1, .quadhd_io_num = -1, .max_transfer_sz = W * STRIP * 2};
    ESP_ERROR_CHECK(spi_bus_initialize(SPI2_HOST, &bus, SPI_DMA_CH_AUTO));
    esp_lcd_panel_io_handle_t io = NULL;
    esp_lcd_panel_io_spi_config_t io_cfg = {.cs_gpio_num = NT_LCD_CS, .dc_gpio_num = NT_LCD_DC, .spi_mode = 3, .pclk_hz = 40 * 1000 * 1000, .trans_queue_depth = 10, .lcd_cmd_bits = 8, .lcd_param_bits = 8,
                                            .on_color_trans_done = on_color_done};
    ESP_ERROR_CHECK(esp_lcd_new_panel_io_spi((esp_lcd_spi_bus_handle_t)SPI2_HOST, &io_cfg, &io));
    esp_lcd_panel_handle_t panel = NULL;
    esp_lcd_panel_dev_config_t pc = {.reset_gpio_num = NT_LCD_RST, .rgb_ele_order = LCD_RGB_ELEMENT_ORDER_RGB, .bits_per_pixel = 16};
    ESP_ERROR_CHECK(esp_lcd_new_panel_st7789(io, &pc, &panel));
    ESP_ERROR_CHECK(esp_lcd_panel_reset(panel));
    ESP_ERROR_CHECK(esp_lcd_panel_init(panel));
    ESP_ERROR_CHECK(esp_lcd_panel_invert_color(panel, true));
    ESP_ERROR_CHECK(esp_lcd_panel_disp_on_off(panel, true));
    uint16_t *buf = heap_caps_malloc(W * STRIP * 2, MALLOC_CAP_DMA);
    if (!buf) {
        ESP_LOGE(TAG, "no DMA buffer");
        return;
    }
    backlight_init();

    // buttons: input with pull-up, pressed = low (vendor demo uses active level 0)
    const int pins[3] = {NT_BTN_BOOT, NT_BTN_PWR, NT_BTN_PLUS};
    const char *names[3] = {"BOOT/-", "PWR", "+/KEY"};
    gpio_config_t bc = {.mode = GPIO_MODE_INPUT, .pull_up_en = GPIO_PULLUP_ENABLE};
    for (int i = 0; i < 3; i++) {
        bc.pin_bit_mask = 1ULL << pins[i];
        ESP_ERROR_CHECK(gpio_config(&bc));
    }

    // touch reset pulse, INT as input
    gpio_config_t rc = {.mode = GPIO_MODE_OUTPUT, .pin_bit_mask = 1ULL << NT_TOUCH_RST};
    ESP_ERROR_CHECK(gpio_config(&rc));
    gpio_config_t ic = {.mode = GPIO_MODE_INPUT, .pull_up_en = GPIO_PULLUP_ENABLE, .pin_bit_mask = 1ULL << NT_TOUCH_INT};
    ESP_ERROR_CHECK(gpio_config(&ic));
    gpio_set_level(NT_TOUCH_RST, 0);
    vTaskDelay(pdMS_TO_TICKS(10));
    gpio_set_level(NT_TOUCH_RST, 1);
    vTaskDelay(pdMS_TO_TICKS(60));

    i2c_master_bus_handle_t bus_i2c = NULL;
    i2c_master_bus_config_t ib = {.i2c_port = I2C_NUM_0, .sda_io_num = NT_I2C_SDA, .scl_io_num = NT_I2C_SCL, .clk_source = I2C_CLK_SRC_DEFAULT, .glitch_ignore_cnt = 7, .flags.enable_internal_pullup = true};
    ESP_ERROR_CHECK(i2c_new_master_bus(&ib, &bus_i2c));
    for (int a = 0x08; a < 0x78; a++)
        if (i2c_master_probe(bus_i2c, a, 50) == ESP_OK) ESP_LOGI(TAG, "I2C device answers at 0x%02X", a);
    i2c_master_dev_handle_t tp = NULL;
    i2c_device_config_t dc = {.dev_addr_length = I2C_ADDR_BIT_LEN_7, .device_address = TOUCH_ADDR, .scl_speed_hz = 400000};
    ESP_ERROR_CHECK(i2c_master_bus_add_device(bus_i2c, &dc, &tp));

    state_t cur = {0}, last = {.tx = -1};
    render(panel, buf, &cur);
    last = cur;
    int fails = 0;
    for (;;) {
        for (int i = 0; i < 3; i++) cur.btn[i] = gpio_get_level(pins[i]) == 0;
        uint8_t reg = 0x01, d[6] = {0};
        if (i2c_master_transmit_receive(tp, &reg, 1, d, 6, 50) == ESP_OK) {
            cur.touch = (d[1] & 0x0F) > 0;
            if (cur.touch) {
                cur.tx = ((d[2] & 0x0F) << 8) | d[3];
                cur.ty = ((d[4] & 0x0F) << 8) | d[5];
            }
        } else {
            cur.touch = false;
            if (++fails % 200 == 1) ESP_LOGW(TAG, "touch read failed (%d so far; the chip may be asleep)", fails);
        }
        bool changed = cur.touch != last.touch || (cur.touch && (cur.tx != last.tx || cur.ty != last.ty));
        for (int i = 0; i < 3; i++)
            if (cur.btn[i] != last.btn[i]) {
                changed = true;
                ESP_LOGI(TAG, "button %s (GPIO%d) %s", names[i], pins[i], cur.btn[i] ? "PRESSED" : "released");
            }
        if (cur.touch && (!last.touch || cur.tx != last.tx || cur.ty != last.ty)) ESP_LOGI(TAG, "touch x=%d y=%d", cur.tx, cur.ty);
        if (changed) {
            render(panel, buf, &cur);
            last = cur;
        }
        vTaskDelay(pdMS_TO_TICKS(15));
    }
}
