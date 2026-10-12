// Audio test: +/KEY plays a soft 1 s tone, BOOT/- records 3 s from the microphone and prints a level bar, PWR plays the recording back softly.
// Codec setup follows the vendor demo (bsp_codec.c): ES8311 speaker codec at the I2C bus, ES7210 mic ADC, I2S0 duplex at 16 kHz, speaker amp enable on GPIO7.
// Volume is capped here (firmware owns audio safety). Pins come from firmware/hardware/board_waveshare_esp32s3_lcd154.h. Output only through the serial monitor.
#include <math.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include "board_waveshare_esp32s3_lcd154.h"
#include "driver/gpio.h"
#include "driver/i2c_master.h"
#include "driver/i2s_std.h"
#include "driver/i2s_tdm.h"
#include "esp_codec_dev.h"
#include "esp_codec_dev_defaults.h"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

static const char *TAG = "nt_audio_test";
#define RATE 16000
#define OUT_VOLUME 35      // 0..100 in esp_codec_dev units; kept low on purpose
#define MIC_GAIN_DB 30.0
#define REC_SECONDS 3
#define REC_BYTES (RATE * REC_SECONDS * 2)

static i2s_chan_handle_t tx, rx;
static esp_codec_dev_handle_t out_dev, in_dev;

static void audio_init(i2c_master_bus_handle_t bus)
{
    i2s_chan_config_t cc = {.id = I2S_NUM_0, .role = I2S_ROLE_MASTER, .dma_desc_num = 6, .dma_frame_num = 240, .auto_clear_after_cb = true, .auto_clear_before_cb = false, .intr_priority = 0};
    ESP_ERROR_CHECK(i2s_new_channel(&cc, &tx, &rx));
    i2s_std_config_t std = {
        .clk_cfg = {.sample_rate_hz = RATE, .clk_src = I2S_CLK_SRC_DEFAULT, .ext_clk_freq_hz = 0, .mclk_multiple = I2S_MCLK_MULTIPLE_256},
        .slot_cfg = {.data_bit_width = I2S_DATA_BIT_WIDTH_16BIT, .slot_bit_width = I2S_SLOT_BIT_WIDTH_AUTO, .slot_mode = I2S_SLOT_MODE_STEREO, .slot_mask = I2S_STD_SLOT_BOTH, .ws_width = I2S_DATA_BIT_WIDTH_16BIT, .ws_pol = false, .bit_shift = true, .left_align = true, .big_endian = false, .bit_order_lsb = false},
        .gpio_cfg = {.mclk = NT_I2S_MCLK, .bclk = NT_I2S_BCLK, .ws = NT_I2S_LRCK, .dout = NT_I2S_DOUT, .din = I2S_GPIO_UNUSED, .invert_flags = {0}}};
    i2s_tdm_config_t tdm = {
        .clk_cfg = {.sample_rate_hz = RATE, .clk_src = I2S_CLK_SRC_DEFAULT, .ext_clk_freq_hz = 0, .mclk_multiple = I2S_MCLK_MULTIPLE_256, .bclk_div = 8},
        .slot_cfg = {.data_bit_width = I2S_DATA_BIT_WIDTH_16BIT, .slot_bit_width = I2S_SLOT_BIT_WIDTH_AUTO, .slot_mode = I2S_SLOT_MODE_STEREO, .slot_mask = I2S_TDM_SLOT0 | I2S_TDM_SLOT1 | I2S_TDM_SLOT2 | I2S_TDM_SLOT3, .ws_width = I2S_TDM_AUTO_WS_WIDTH, .ws_pol = false, .bit_shift = true, .left_align = false, .big_endian = false, .bit_order_lsb = false, .skip_mask = false, .total_slot = I2S_TDM_AUTO_SLOT_NUM},
        .gpio_cfg = {.mclk = NT_I2S_MCLK, .bclk = NT_I2S_BCLK, .ws = NT_I2S_LRCK, .dout = I2S_GPIO_UNUSED, .din = NT_I2S_DIN, .invert_flags = {0}}};
    ESP_ERROR_CHECK(i2s_channel_init_std_mode(tx, &std));
    ESP_ERROR_CHECK(i2s_channel_init_tdm_mode(rx, &tdm));

    audio_codec_i2s_cfg_t i2s_cfg = {.port = I2S_NUM_0, .rx_handle = rx, .tx_handle = tx};
    const audio_codec_data_if_t *data_if = audio_codec_new_i2s_data(&i2s_cfg);
    audio_codec_i2c_cfg_t i2c_cfg = {.addr = ES8311_CODEC_DEFAULT_ADDR, .bus_handle = bus};
    const audio_codec_ctrl_if_t *out_ctrl = audio_codec_new_i2c_ctrl(&i2c_cfg);
    const audio_codec_gpio_if_t *gpio_if = audio_codec_new_gpio();
    es8311_codec_cfg_t e8 = {.ctrl_if = out_ctrl, .gpio_if = gpio_if, .codec_mode = ESP_CODEC_DEV_WORK_MODE_DAC, .pa_pin = NT_PA_CTRL, .use_mclk = true};
    e8.hw_gain.pa_voltage = 5.0;
    e8.hw_gain.codec_dac_voltage = 3.3;
    const audio_codec_if_t *out_codec = es8311_codec_new(&e8);
    esp_codec_dev_cfg_t dc = {.dev_type = ESP_CODEC_DEV_TYPE_OUT, .codec_if = out_codec, .data_if = data_if};
    out_dev = esp_codec_dev_new(&dc);

    i2c_cfg.addr = ES7210_CODEC_DEFAULT_ADDR;
    const audio_codec_ctrl_if_t *in_ctrl = audio_codec_new_i2c_ctrl(&i2c_cfg);
    es7210_codec_cfg_t e7 = {.ctrl_if = in_ctrl, .mic_selected = ES7210_SEL_MIC1 | ES7210_SEL_MIC2 | ES7210_SEL_MIC3 | ES7210_SEL_MIC4};
    const audio_codec_if_t *in_codec = es7210_codec_new(&e7);
    dc.dev_type = ESP_CODEC_DEV_TYPE_IN;
    dc.codec_if = in_codec;
    in_dev = esp_codec_dev_new(&dc);
    if (!out_dev || !in_dev) {
        ESP_LOGE(TAG, "codec device creation failed (out=%p in=%p)", out_dev, in_dev);
        abort();
    }

    esp_codec_dev_sample_info_t fs = {.bits_per_sample = 16, .channel = 1, .channel_mask = 0, .sample_rate = RATE, .mclk_multiple = 0};
    ESP_ERROR_CHECK(esp_codec_dev_open(out_dev, &fs));
    ESP_ERROR_CHECK(esp_codec_dev_set_out_vol(out_dev, 0));  // silent until we play
    fs.channel = 4;
    fs.channel_mask = ESP_CODEC_DEV_MAKE_CHANNEL_MASK(0);
    ESP_ERROR_CHECK(esp_codec_dev_open(in_dev, &fs));
    ESP_ERROR_CHECK(esp_codec_dev_set_in_channel_gain(in_dev, ESP_CODEC_DEV_MAKE_CHANNEL_MASK(0), MIC_GAIN_DB));
}

static void play(const int16_t *s, size_t samples)
{
    esp_codec_dev_set_out_vol(out_dev, OUT_VOLUME);
    const size_t chunk = 1600;  // 100 ms
    for (size_t i = 0; i < samples; i += chunk) {
        size_t n = (samples - i) < chunk ? (samples - i) : chunk;
        int err = esp_codec_dev_write(out_dev, (void *)(s + i), n * 2);
        if (err != ESP_CODEC_DEV_OK) {
            ESP_LOGE(TAG, "write error %d", err);
            break;
        }
    }
    esp_codec_dev_set_out_vol(out_dev, 0);
}

static void tone(void)
{
    const int n = RATE;  // 1 second
    int16_t *b = heap_caps_malloc(n * 2, MALLOC_CAP_INTERNAL);
    if (!b) return;
    for (int i = 0; i < n; i++) {
        float env = i < 800 ? i / 800.0f : (i > n - 800 ? (n - i) / 800.0f : 1.0f);  // fade in and out, no clicks
        b[i] = (int16_t)(6000 * env * sinf(2.0f * (float)M_PI * 440.0f * i / RATE));
    }
    ESP_LOGI(TAG, "playing a 440 Hz tone, volume %d", OUT_VOLUME);
    play(b, n);
    free(b);
}

static int16_t *rec;

static void record(void)
{
    if (!rec) rec = heap_caps_malloc(REC_BYTES, MALLOC_CAP_INTERNAL);
    if (!rec) {
        ESP_LOGE(TAG, "no memory for the recording");
        return;
    }
    ESP_LOGI(TAG, "recording %d s: speak now", REC_SECONDS);
    esp_codec_dev_set_in_gain(in_dev, MIC_GAIN_DB);
    const size_t chunk = RATE / 10;  // 100 ms
    int peak = 0;
    for (size_t i = 0; i < (size_t)RATE * REC_SECONDS; i += chunk) {
        int err = esp_codec_dev_read(in_dev, rec + i, chunk * 2);
        if (err != ESP_CODEC_DEV_OK) {
            ESP_LOGE(TAG, "read error %d", err);
            return;
        }
        double sum = 0;
        for (size_t k = 0; k < chunk; k++) sum += (double)rec[i + k] * rec[i + k];
        int rms = (int)sqrt(sum / chunk);
        if (rms > peak) peak = rms;
        char bar[41] = {0};
        int len = rms / 150;
        if (len > 40) len = 40;
        memset(bar, '#', len);
        printf("mic level %5d %s\n", rms, bar);
    }
    ESP_LOGI(TAG, "recording done, loudest 100 ms RMS = %d (silence is usually a few dozen, speech a few thousand)", peak);
}

void app_main(void)
{
    i2c_master_bus_handle_t bus = NULL;
    i2c_master_bus_config_t ib = {.i2c_port = I2C_NUM_0, .sda_io_num = NT_I2C_SDA, .scl_io_num = NT_I2C_SCL, .clk_source = I2C_CLK_SRC_DEFAULT, .glitch_ignore_cnt = 7, .flags.enable_internal_pullup = true};
    ESP_ERROR_CHECK(i2c_new_master_bus(&ib, &bus));
    audio_init(bus);
    const int pins[3] = {NT_BTN_BOOT, NT_BTN_PWR, NT_BTN_PLUS};
    gpio_config_t bc = {.mode = GPIO_MODE_INPUT, .pull_up_en = GPIO_PULLUP_ENABLE};
    for (int i = 0; i < 3; i++) {
        bc.pin_bit_mask = 1ULL << pins[i];
        ESP_ERROR_CHECK(gpio_config(&bc));
    }
    ESP_LOGI(TAG, "ready: +/KEY = tone, BOOT/- = record 3 s, PWR (short press) = play it back");
    bool prev[3] = {0};
    for (;;) {
        bool now[3];
        for (int i = 0; i < 3; i++) now[i] = gpio_get_level(pins[i]) == 0;
        if (now[2] && !prev[2]) tone();
        if (now[0] && !prev[0]) record();
        if (now[1] && !prev[1]) {
            if (!rec) ESP_LOGW(TAG, "nothing recorded yet");
            else {
                ESP_LOGI(TAG, "playing back the recording");
                play(rec, RATE * REC_SECONDS);
            }
        }
        for (int i = 0; i < 3; i++) prev[i] = now[i];
        vTaskDelay(pdMS_TO_TICKS(20));
    }
}
