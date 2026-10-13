// Voice test firmware (v0.3 of our device firmware): the host plays speech through the speaker (audio.play + binary frames) and can capture the microphone (audio.start_capture).
// Rules implemented from docs/audio.md: strict frame parsing, in-order sequence numbers, one playback and one capture at a time, ~2 s buffer, 5 s silent-host timeout, rebuffer after an underrun,
// completion only after the audio has really played, the microphone is MUTED at boot and only the physical +/KEY button (GPIO4) can unmute it, the host can never unmute.
// Everything else is answered with command.failed (honest "not implemented"). Wi-Fi name/password and the device key are in the local, git-ignored sdkconfig.
// Not compiled or run by the author when written; pins and codec setup come from the vendor demo and our audio_test.
#include <stdio.h>
#include <string.h>
#include <time.h>
#include "esp_event.h"
#include "esp_log.h"
#include "esp_netif.h"
#include "esp_random.h"
#include "mbedtls/md.h"
#include "esp_netif_sntp.h"
#include "cJSON.h"
#include "esp_websocket_client.h"
#include "esp_wifi.h"
#include "freertos/FreeRTOS.h"
#include "freertos/event_groups.h"
#include "nvs_flash.h"
#include <math.h>
#include <stdlib.h>
#include "board_waveshare_esp32s3_lcd154.h"
#include "driver/gpio.h"
#include "driver/i2c_master.h"
#include "driver/i2s_std.h"
#include "driver/i2s_tdm.h"
#include "esp_codec_dev.h"
#include "esp_codec_dev_defaults.h"
#include "esp_timer.h"
#include "freertos/ringbuf.h"
#include "freertos/semphr.h"
#include "freertos/task.h"

static const char *TAG = "nt_voice";
extern const char ca_pem_start[] asm("_binary_ca_pem_start");
static EventGroupHandle_t s_ev;
#define GOT_IP BIT0

static void wifi_event(void *arg, esp_event_base_t base, int32_t id, void *data)
{
    if (base == WIFI_EVENT && id == WIFI_EVENT_STA_START) esp_wifi_connect();
    else if (base == WIFI_EVENT && id == WIFI_EVENT_STA_DISCONNECTED) {
        wifi_event_sta_disconnected_t *d = data;
        ESP_LOGW(TAG, "Wi-Fi disconnected (reason %d), retrying", d->reason);
        xEventGroupClearBits(s_ev, GOT_IP);
        vTaskDelay(pdMS_TO_TICKS(1000));
        esp_wifi_connect();
    } else if (base == IP_EVENT && id == IP_EVENT_STA_GOT_IP) {
        ip_event_got_ip_t *e = data;
        ESP_LOGI(TAG, "Wi-Fi up, address " IPSTR, IP2STR(&e->ip_info.ip));
        xEventGroupSetBits(s_ev, GOT_IP);
    }
}

// Signed first message (device.ready). Protocol v1: mac = HMAC-SHA256(key, canonical JSON of the message without auth.mac), canonical = sorted keys, no spaces.
// The signing string below is written out with the keys already in sorted order, matching Python's json.dumps(sort_keys=True, separators=(",", ":")).
static int hex_to_bytes(const char *hex, uint8_t *out, size_t n)
{
    if (strlen(hex) != n * 2) return -1;
    for (size_t i = 0; i < n; i++) {
        unsigned v;
        if (sscanf(hex + 2 * i, "%2x", &v) != 1) return -1;
        out[i] = (uint8_t)v;
    }
    return 0;
}

static bool load_key(uint8_t key[32])
{
    if (hex_to_bytes(CONFIG_NT_DEVICE_KEY_HEX, key, 32) != 0) {
        ESP_LOGE(TAG, "device key missing or not 64 hex characters: run tools/fw_config.py or idf.py menuconfig");
        return false;
    }
    return true;
}

// Builds, signs and sends one message. payload_sorted must be a JSON object text whose keys are ALREADY in sorted order (we write them by hand).
static void send_signed(esp_websocket_client_handle_t c, const char *type, const char *id_prefix, const char *payload_sorted)
{
    uint8_t key[32];
    if (!load_key(key)) return;
    char nonce[25], mid[17];
    snprintf(nonce, sizeof nonce, "%08x%08x%08x", (unsigned)esp_random(), (unsigned)esp_random(), (unsigned)esp_random());
    snprintf(mid, sizeof mid, "%s%08x", id_prefix, (unsigned)esp_random());
    long long ts = (long long)time(NULL);
    enum { BODY_MAX = 768, OUT_MAX = 900 };
    char *body = malloc(BODY_MAX), *out = malloc(OUT_MAX);   // heap, not stack: this runs inside the websocket task
    if (!body || !out) {
        free(body);
        free(out);
        ESP_LOGE(TAG, "out of memory");
        return;
    }
    const char *fmt_head = "{\"auth\":{\"kid\":1,%s\"nonce\":\"%s\",\"ts\":%lld},\"device_id\":\"%s\",\"message_id\":\"%s\",\"payload\":";
    const char *fmt_tail = ",\"protocol_version\":1,\"type\":\"%s\"}";
    int n = snprintf(body, BODY_MAX, fmt_head, "", nonce, ts, CONFIG_NT_DEVICE_ID, mid);
    n += snprintf(body + n, BODY_MAX - n, "%s", payload_sorted);
    n += snprintf(body + n, BODY_MAX - n, fmt_tail, type);
    if (n >= BODY_MAX) {
        ESP_LOGE(TAG, "message too long");
        free(body);
        free(out);
        return;
    }
    uint8_t mac[32];
    if (mbedtls_md_hmac(mbedtls_md_info_from_type(MBEDTLS_MD_SHA256), key, 32, (const unsigned char *)body, n, mac) != 0) {
        ESP_LOGE(TAG, "HMAC failed");
        free(body);
        free(out);
        return;
    }
    char macx[65], mac_field[96];
    for (int i = 0; i < 32; i++) sprintf(macx + 2 * i, "%02x", mac[i]);
    snprintf(mac_field, sizeof mac_field, "\"mac\":\"%s\",", macx);   // the receiver rebuilds the canonical form itself, so key order here does not matter
    int m = snprintf(out, OUT_MAX, fmt_head, mac_field, nonce, ts, CONFIG_NT_DEVICE_ID, mid);
    m += snprintf(out + m, OUT_MAX - m, "%s", payload_sorted);
    m += snprintf(out + m, OUT_MAX - m, fmt_tail, type);
    int sent = esp_websocket_client_send_text(c, out, m, pdMS_TO_TICKS(5000));
    ESP_LOGI(TAG, "sent signed %s (%d bytes)", type, sent);
    free(body);
    free(out);
}

static void send_hello(esp_websocket_client_handle_t c)
{
    send_signed(c, "device.ready", "hello", "{\"boot_cause\":\"power_on\",\"firmware_version\":\"nt-voice-0.3\",\"protocol_versions\":[1],\"recovered_state\":false}");
}

// ---- incoming: verify first, act second. Anything that fails a check is dropped silently (counted, never answered).
static char s_seen[32][25];
static int s_seen_next;
static int s_dropped;

static bool nonce_seen(const char *nonce)
{
    for (int i = 0; i < 32; i++)
        if (strcmp(s_seen[i], nonce) == 0) return true;
    strlcpy(s_seen[s_seen_next], nonce, 25);
    s_seen_next = (s_seen_next + 1) % 32;
    return false;
}

// The host's encoder writes canonical JSON (sorted keys, no spaces) with auth.mac included. The signed bytes are that same text with the `"mac":"<64 hex>",` member removed,
// so we verify over the received bytes directly and never re-serialize.
static bool verify_incoming(const char *txt, int n, cJSON **root_out)
{
    const char *p = NULL;
    for (int i = 0; i + 73 <= n; i++)
        if (memcmp(txt + i, "\"mac\":\"", 7) == 0 && txt[i + 71] == '"' && txt[i + 72] == ',') {
            p = txt + i;
            break;
        }
    if (!p) return false;
    uint8_t want[32], key[32];
    char hex[65];
    memcpy(hex, p + 7, 64);
    hex[64] = 0;
    if (hex_to_bytes(hex, want, 32) != 0 || !load_key(key)) return false;
    char *stripped = malloc(n);
    if (!stripped) return false;
    int pre = (int)(p - txt), m = 0;
    memcpy(stripped, txt, pre);
    memcpy(stripped + pre, p + 73, n - pre - 73);
    m = n - 73;
    uint8_t got[32];
    int rc = mbedtls_md_hmac(mbedtls_md_info_from_type(MBEDTLS_MD_SHA256), key, 32, (const unsigned char *)stripped, m, got);
    free(stripped);
    if (rc != 0) return false;
    uint8_t diff = 0;
    for (int i = 0; i < 32; i++) diff |= got[i] ^ want[i];
    if (diff) return false;
    cJSON *root = cJSON_ParseWithLength(txt, n);
    if (!root) return false;
    cJSON *auth = cJSON_GetObjectItem(root, "auth"), *did = cJSON_GetObjectItem(root, "device_id");
    cJSON *ts = auth ? cJSON_GetObjectItem(auth, "ts") : NULL, *nonce = auth ? cJSON_GetObjectItem(auth, "nonce") : NULL, *kid = auth ? cJSON_GetObjectItem(auth, "kid") : NULL;
    bool ok = cJSON_IsString(did) && strcmp(did->valuestring, CONFIG_NT_DEVICE_ID) == 0 && cJSON_IsNumber(ts) && cJSON_IsString(nonce) && strlen(nonce->valuestring) <= 24 &&
              (!kid || (cJSON_IsNumber(kid) && kid->valueint == 1));
    if (ok) {
        long long now = (long long)time(NULL), d = now - (long long)ts->valuedouble;
        ok = (d < 30 && d > -30) && !nonce_seen(nonce->valuestring);   // 30 s window and single-use nonces
    }
    if (!ok) {
        cJSON_Delete(root);
        return false;
    }
    *root_out = root;
    return true;
}

static bool safe_ident(const char *s)
{
    size_t n = strlen(s);
    if (n < 8 || n > 64) return false;
    for (size_t i = 0; i < n; i++)
        if (!((s[i] >= '0' && s[i] <= '9') || (s[i] >= 'a' && s[i] <= 'z') || (s[i] >= 'A' && s[i] <= 'Z') || s[i] == '_' || s[i] == '-')) return false;
    return true;
}


// ======================= audio hardware (same setup as firmware/audio_test) =======================
#define RATE 16000
#define OUT_VOLUME_MAX 90      // hard cap on the codec volume scale (a dB-like scale; 65 to 90 is audible on this board)
#define MIC_GAIN_DB 30.0
#define CAPTURE_DIGITAL_GAIN 2 // software gain on the averaged mic channels, clipped (3 reached full scale on the owner's unit)
#define RING_BYTES (64 * 1024) // ~2 s of PCM16 16 kHz
#define PREBUFFER_BYTES 4800   // 150 ms before playback starts or resumes
#define MAX_PLAY_BYTES (30 * RATE * 2)
#define PLAY_IDLE_TIMEOUT_US (5 * 1000 * 1000)

static esp_codec_dev_handle_t out_dev, in_dev;
static esp_websocket_client_handle_t g_ws;
static SemaphoreHandle_t g_mu;
static RingbufHandle_t g_ring;
static volatile bool g_mic_enabled;   // muted at boot, always
static bool g_mic_hw_ok;              // false if the microphone chip never answered at boot: the speaker keeps working

static void audio_init(i2c_master_bus_handle_t bus)
{
    i2s_chan_handle_t tx, rx;
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
        ESP_LOGE(TAG, "codec device creation failed");
        abort();
    }
    esp_codec_dev_sample_info_t fs = {.bits_per_sample = 16, .channel = 1, .channel_mask = 0, .sample_rate = RATE, .mclk_multiple = 0};
    ESP_ERROR_CHECK(esp_codec_dev_open(out_dev, &fs));
    ESP_ERROR_CHECK(esp_codec_dev_set_out_vol(out_dev, 0));  // silent until a verified audio.play
    fs.channel = 4;
    fs.channel_mask = ESP_CODEC_DEV_MAKE_CHANNEL_MASK(0) | ESP_CODEC_DEV_MAKE_CHANNEL_MASK(1) | ESP_CODEC_DEV_MAKE_CHANNEL_MASK(2) | ESP_CODEC_DEV_MAKE_CHANNEL_MASK(3);
    // The microphone chip sometimes does not answer on the first try after a reset (seen once: I2C nack). Retry, and never let it take the speaker down.
    for (int attempt = 1; attempt <= 6 && !g_mic_hw_ok; attempt++) {
        if (esp_codec_dev_open(in_dev, &fs) == ESP_CODEC_DEV_OK) {
            g_mic_hw_ok = esp_codec_dev_set_in_channel_gain(in_dev, fs.channel_mask, MIC_GAIN_DB) == ESP_CODEC_DEV_OK;
        } else {
            ESP_LOGW(TAG, "microphone chip did not start (attempt %d of 6)", attempt);
            esp_codec_dev_close(in_dev);
            vTaskDelay(pdMS_TO_TICKS(200));
        }
    }
    if (!g_mic_hw_ok) ESP_LOGE(TAG, "microphone chip never answered: capture is disabled until the next reboot, playback still works");
}

// ======================= binary audio frames (protocol/audio.py) =======================
static void put_be16(uint8_t *p, uint16_t v) { p[0] = v >> 8; p[1] = v & 0xff; }
static void put_be32(uint8_t *p, uint32_t v) { p[0] = v >> 24; p[1] = v >> 16; p[2] = v >> 8; p[3] = v; }

// ======================= playback =======================
typedef struct {
    bool active, ended, playing;
    char id[65], cmd[65];
    uint16_t stream_no;
    uint32_t next_seq;
    size_t received, consumed;
    int underruns, volume;
    int64_t last_rx_us;
} play_t;
static play_t pb;
static int s_audio_rejected;

static void finish_playback(const char *result, const char *fail_code, const char *fail_msg)
{
    xSemaphoreTake(g_mu, portMAX_DELAY);
    if (!pb.active) {
        xSemaphoreGive(g_mu);
        return;
    }
    play_t c = pb;
    pb.active = false;
    xSemaphoreGive(g_mu);
    size_t n;
    void *junk;
    while ((junk = xRingbufferReceiveUpTo(g_ring, &n, 0, 4096)) != NULL) vRingbufferReturnItem(g_ring, junk);   // drop whatever is left
    esp_codec_dev_set_out_vol(out_dev, 0);
    char p[240];
    snprintf(p, sizeof p, "{\"bytes_played\":%u,\"result\":\"%s\",\"stream_id\":\"%s\",\"underruns\":%d}", (unsigned)c.consumed, result, c.id, c.underruns);
    send_signed(g_ws, "audio.playback.finished", "pbfin", p);
    if (fail_code) {
        snprintf(p, sizeof p, "{\"code\":\"%s\",\"command_id\":\"%s\",\"message\":\"%s\"}", fail_code, c.cmd, fail_msg);
        send_signed(g_ws, "command.failed", "fail", p);
    } else {
        snprintf(p, sizeof p, "{\"command_id\":\"%s\"}", c.cmd);
        send_signed(g_ws, "command.completed", "done", p);
    }
    ESP_LOGI(TAG, "playback %s: %u bytes, %d underruns", result, (unsigned)c.consumed, c.underruns);
}

static void playback_task(void *arg)
{
    for (;;) {
        xSemaphoreTake(g_mu, portMAX_DELAY);
        bool active = pb.active, playing = pb.playing, ended = pb.ended;
        size_t buffered = pb.received - pb.consumed;
        int64_t idle = esp_timer_get_time() - pb.last_rx_us;
        xSemaphoreGive(g_mu);
        if (!active) {
            vTaskDelay(pdMS_TO_TICKS(20));
            continue;
        }
        if (!ended && idle > PLAY_IDLE_TIMEOUT_US) {
            finish_playback("error", "timeout", "no audio data from host");
            continue;
        }
        if (!playing) {
            if (buffered >= PREBUFFER_BYTES || (ended && buffered > 0)) {
                xSemaphoreTake(g_mu, portMAX_DELAY);
                pb.playing = true;
                xSemaphoreGive(g_mu);
            } else if (ended && buffered == 0) {
                finish_playback("completed", NULL, NULL);
            } else {
                vTaskDelay(pdMS_TO_TICKS(10));
            }
            continue;
        }
        size_t got = 0;
        void *item = xRingbufferReceiveUpTo(g_ring, &got, pdMS_TO_TICKS(20), 1920);
        if (item) {
            esp_codec_dev_write(out_dev, item, got);   // blocks about as long as the audio lasts: this paces playback
            vRingbufferReturnItem(g_ring, item);
            xSemaphoreTake(g_mu, portMAX_DELAY);
            pb.consumed += got;
            xSemaphoreGive(g_mu);
        } else if (ended) {
            finish_playback("completed", NULL, NULL);
        } else {
            xSemaphoreTake(g_mu, portMAX_DELAY);
            pb.underruns++;
            pb.playing = false;    // ran dry: rebuffer before resuming
            xSemaphoreGive(g_mu);
        }
    }
}

static void on_host_binary(const uint8_t *d, int n)
{
    if (n < 12 || d[0] != 1 || d[1] != 2 || (d[8] & ~1) || d[9] != 0) { s_audio_rejected++; return; }   // version 1, playback kind, only the END flag, pcm16
    uint16_t stream_no = (d[2] << 8) | d[3], plen = (d[10] << 8) | d[11];
    uint32_t seq = ((uint32_t)d[4] << 24) | (d[5] << 16) | (d[6] << 8) | d[7];
    if (stream_no == 0 || plen > 4096 || n - 12 != plen || (plen & 1)) { s_audio_rejected++; return; }
    xSemaphoreTake(g_mu, portMAX_DELAY);
    if (!pb.active || pb.stream_no != stream_no || pb.ended) {
        xSemaphoreGive(g_mu);
        s_audio_rejected++;
        return;
    }
    const char *fail = NULL, *msg = NULL;
    if (seq != pb.next_seq) { fail = "invalid_state"; msg = "audio sequence gap"; }
    else if (pb.received + plen > MAX_PLAY_BYTES) { fail = "busy"; msg = "audio too long"; }
    else if (plen && xRingbufferSend(g_ring, d + 12, plen, 0) != pdTRUE) { fail = "busy"; msg = "audio buffer overrun"; }
    if (!fail) {
        pb.next_seq++;
        pb.received += plen;
        pb.last_rx_us = esp_timer_get_time();
        if (d[8] & 1) pb.ended = true;
    }
    xSemaphoreGive(g_mu);
    if (fail) finish_playback("error", fail, msg);
}

// ======================= capture =======================
typedef struct {
    bool active, stop;
    char id[65];
    uint16_t stream_no;
    int max_seconds;
    const char *reason;
} cap_t;
static cap_t cap;

static void capture_task(void *arg)
{
    const int FRAMES = 960;                               // 60 ms at 16 kHz
    int16_t *raw = malloc(FRAMES * 4 * 2);
    uint8_t *frame = malloc(12 + FRAMES * 2);
    if (!raw || !frame) {
        ESP_LOGE(TAG, "capture buffers failed");
        vTaskDelete(NULL);
    }
    for (;;) {
        xSemaphoreTake(g_mu, portMAX_DELAY);
        bool active = cap.active;
        xSemaphoreGive(g_mu);
        if (!active) {
            vTaskDelay(pdMS_TO_TICKS(20));
            continue;
        }
        uint32_t seq = 0;
        int64_t t0 = esp_timer_get_time();
        for (int k = 0; k < 3; k++) esp_codec_dev_read(in_dev, raw, FRAMES * 4 * 2);   // drop stale audio
        const char *reason = "host_request";
        for (;;) {
            xSemaphoreTake(g_mu, portMAX_DELAY);
            bool stop = cap.stop;
            if (stop && cap.reason) reason = cap.reason;
            int maxs = cap.max_seconds;
            uint16_t sn = cap.stream_no;
            xSemaphoreGive(g_mu);
            if (!g_mic_enabled) { reason = "muted"; break; }
            if (stop) break;
            if ((esp_timer_get_time() - t0) / 1000000 >= maxs) { reason = "timeout"; break; }
            if (esp_codec_dev_read(in_dev, raw, FRAMES * 4 * 2) != ESP_CODEC_DEV_OK) { reason = "host_request"; break; }
            frame[0] = 1; frame[1] = 1; put_be16(frame + 2, sn); put_be32(frame + 4, seq++); frame[8] = 0; frame[9] = 0; put_be16(frame + 10, FRAMES * 2);
            for (int i = 0; i < FRAMES; i++) {
                int v = ((int)raw[i * 4] + (int)raw[i * 4 + 1]) / 2 * CAPTURE_DIGITAL_GAIN;   // microphones 1 and 2 averaged
                if (v > 32767) v = 32767;
                if (v < -32768) v = -32768;
                frame[12 + i * 2] = v & 0xff;
                frame[13 + i * 2] = (v >> 8) & 0xff;
            }
            esp_websocket_client_send_bin(g_ws, (const char *)frame, 12 + FRAMES * 2, pdMS_TO_TICKS(2000));
        }
        xSemaphoreTake(g_mu, portMAX_DELAY);
        uint16_t sn = cap.stream_no;
        char id[65];
        strlcpy(id, cap.id, sizeof id);
        cap.active = false;
        cap.stop = false;
        xSemaphoreGive(g_mu);
        frame[0] = 1; frame[1] = 1; put_be16(frame + 2, sn); put_be32(frame + 4, seq); frame[8] = 1; frame[9] = 0; put_be16(frame + 10, 0);   // END frame
        esp_websocket_client_send_bin(g_ws, (const char *)frame, 12, pdMS_TO_TICKS(2000));
        char p[160];
        snprintf(p, sizeof p, "{\"capture_id\":\"%s\",\"reason\":\"%s\"}", id, reason);
        send_signed(g_ws, "audio.capture.stopped", "capstop", p);
        ESP_LOGI(TAG, "capture %s ended: %s", id, reason);
    }
}

// ======================= commands =======================
static void reply_failed(const char *mid, const char *code, const char *msg)
{
    char p[260];
    snprintf(p, sizeof p, "{\"code\":\"%s\",\"command_id\":\"%s\",\"message\":\"%s\"}", code, mid, msg);
    send_signed(g_ws, "command.failed", "fail", p);
}
static void reply_accepted(const char *mid, const char *type)
{
    char p[200];
    snprintf(p, sizeof p, "{\"command_id\":\"%s\",\"command_type\":\"%s\"}", mid, type);
    send_signed(g_ws, "command.accepted", "acc", p);
}
static void reply_completed(const char *mid)
{
    char p[120];
    snprintf(p, sizeof p, "{\"command_id\":\"%s\"}", mid);
    send_signed(g_ws, "command.completed", "done", p);
}

static bool ident_ok(const cJSON *s)
{
    if (!cJSON_IsString(s)) return false;
    size_t n = strlen(s->valuestring);
    if (n < 1 || n > 64) return false;
    for (size_t i = 0; i < n; i++) {
        char c = s->valuestring[i];
        if (!((c >= '0' && c <= '9') || (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || c == '_' || c == '-')) return false;
    }
    return true;
}
static bool int_in(const cJSON *v, int lo, int hi) { return cJSON_IsNumber(v) && v->valuedouble == (double)v->valueint && v->valueint >= lo && v->valueint <= hi; }

static void cmd_audio_play(const char *mid, cJSON *p)
{
    cJSON *sid = cJSON_GetObjectItem(p, "stream_id"), *sno = cJSON_GetObjectItem(p, "stream_no"), *fmt = cJSON_GetObjectItem(p, "format"), *vol = cJSON_GetObjectItem(p, "volume");
    if (!ident_ok(sid) || !int_in(sno, 1, 65535) || !cJSON_IsString(fmt) || (vol && !int_in(vol, 0, 100))) { reply_failed(mid, "invalid_payload", "bad audio.play payload"); return; }
    if (strcmp(fmt->valuestring, "pcm16_16k_mono") != 0) { reply_failed(mid, "unsupported_command", "only pcm16_16k_mono is supported"); return; }
    xSemaphoreTake(g_mu, portMAX_DELAY);
    if (pb.active) {
        xSemaphoreGive(g_mu);
        reply_failed(mid, "busy", "playback already active");
        return;
    }
    memset(&pb, 0, sizeof pb);
    pb.active = true;
    strlcpy(pb.id, sid->valuestring, sizeof pb.id);
    strlcpy(pb.cmd, mid, sizeof pb.cmd);
    pb.stream_no = (uint16_t)sno->valueint;
    pb.volume = vol ? vol->valueint : 70;
    pb.last_rx_us = esp_timer_get_time();
    xSemaphoreGive(g_mu);
    // Test mapping from the host's 0..100 to this board's codec scale: 0 stays silent, otherwise 55..90 (hard cap OUT_VOLUME_MAX). Real loudness is unmeasured.
    int codec_vol = pb.volume == 0 ? 0 : 55 + pb.volume * 35 / 100;
    if (codec_vol > OUT_VOLUME_MAX) codec_vol = OUT_VOLUME_MAX;
    esp_codec_dev_set_out_vol(out_dev, codec_vol);
    reply_accepted(mid, "audio.play");
    ESP_LOGI(TAG, "audio.play accepted: stream %s no %d codec volume %d", pb.id, pb.stream_no, codec_vol);
}

static void cmd_audio_stop_playback(const char *mid, cJSON *p)
{
    cJSON *sid = cJSON_GetObjectItem(p, "stream_id");
    xSemaphoreTake(g_mu, portMAX_DELAY);
    bool match = ident_ok(sid) && pb.active && strcmp(pb.id, sid->valuestring) == 0;
    xSemaphoreGive(g_mu);
    if (!match) { reply_failed(mid, "invalid_state", "no such active playback"); return; }
    reply_accepted(mid, "audio.stop_playback");
    finish_playback("interrupted", NULL, NULL);
    reply_completed(mid);
}

static void cmd_audio_start_capture(const char *mid, cJSON *p)
{
    cJSON *cid = cJSON_GetObjectItem(p, "capture_id"), *sno = cJSON_GetObjectItem(p, "stream_no"), *ms = cJSON_GetObjectItem(p, "max_seconds");
    if (!ident_ok(cid) || !int_in(sno, 1, 65535) || !int_in(ms, 1, 30)) { reply_failed(mid, "invalid_payload", "bad audio.start_capture payload"); return; }
    if (!g_mic_hw_ok) { reply_failed(mid, "internal", "microphone hardware did not start"); return; }
    if (!g_mic_enabled) { reply_failed(mid, "not_allowed", "microphone is muted"); return; }
    xSemaphoreTake(g_mu, portMAX_DELAY);
    if (cap.active) {
        xSemaphoreGive(g_mu);
        reply_failed(mid, "busy", "capture already active");
        return;
    }
    memset(&cap, 0, sizeof cap);
    strlcpy(cap.id, cid->valuestring, sizeof cap.id);
    cap.stream_no = (uint16_t)sno->valueint;
    cap.max_seconds = ms->valueint;
    cap.active = true;
    xSemaphoreGive(g_mu);
    reply_accepted(mid, "audio.start_capture");
    char pl[120];
    snprintf(pl, sizeof pl, "{\"capture_id\":\"%s\"}", cid->valuestring);
    send_signed(g_ws, "audio.capture.started", "capstart", pl);
    reply_completed(mid);
    ESP_LOGI(TAG, "capture %s started (max %d s)", cap.id, cap.max_seconds);
}

static void cmd_audio_stop_capture(const char *mid, cJSON *p)
{
    cJSON *cid = cJSON_GetObjectItem(p, "capture_id");
    xSemaphoreTake(g_mu, portMAX_DELAY);
    bool match = ident_ok(cid) && cap.active && strcmp(cap.id, cid->valuestring) == 0;
    if (match) { cap.stop = true; cap.reason = "host_request"; }
    xSemaphoreGive(g_mu);
    if (!match) { reply_failed(mid, "invalid_state", "no such active capture"); return; }
    reply_accepted(mid, "audio.stop_capture");
    reply_completed(mid);
}

static void on_host_text(esp_websocket_client_handle_t c, const char *txt, int n)
{
    cJSON *root = NULL;
    if (!verify_incoming(txt, n, &root)) {
        if (++s_dropped % 10 == 1) ESP_LOGW(TAG, "dropped an unauthentic or stale message (%d so far); no reply is sent", s_dropped);
        return;
    }
    cJSON *type = cJSON_GetObjectItem(root, "type"), *mid = cJSON_GetObjectItem(root, "message_id"), *payload = cJSON_GetObjectItem(root, "payload");
    if (!cJSON_IsString(type) || !cJSON_IsString(mid) || !safe_ident(mid->valuestring) || strlen(type->valuestring) > 40 || !cJSON_IsObject(payload)) {
        cJSON_Delete(root);
        return;
    }
    const char *t = type->valuestring, *m = mid->valuestring;
    ESP_LOGI(TAG, "VERIFIED command %s (id %s)", t, m);
    if (strcmp(t, "audio.play") == 0) cmd_audio_play(m, payload);
    else if (strcmp(t, "audio.stop_playback") == 0) cmd_audio_stop_playback(m, payload);
    else if (strcmp(t, "audio.start_capture") == 0) cmd_audio_start_capture(m, payload);
    else if (strcmp(t, "audio.stop_capture") == 0) cmd_audio_stop_capture(m, payload);
    else reply_failed(m, "unsupported_command", "not implemented in this test firmware");
    cJSON_Delete(root);
}

static void reset_audio_state(void)
{
    xSemaphoreTake(g_mu, portMAX_DELAY);
    pb.active = false;
    cap.stop = true;
    cap.reason = "host_request";
    xSemaphoreGive(g_mu);
}

static void ws_event(void *arg, esp_event_base_t base, int32_t id, void *data)
{
    esp_websocket_event_data_t *e = data;
    switch (id) {
    case WEBSOCKET_EVENT_CONNECTED:
        ESP_LOGI(TAG, "wss CONNECTED: certificate check against our CA succeeded");
        send_hello((esp_websocket_client_handle_t)arg);
        break;
    case WEBSOCKET_EVENT_DISCONNECTED:
        ESP_LOGW(TAG, "wss disconnected");
        reset_audio_state();
        break;
    case WEBSOCKET_EVENT_DATA:
        if (e->payload_offset != 0 || e->data_len != e->payload_len) {
            ESP_LOGW(TAG, "fragmented frame ignored");
        } else if (e->op_code == 0x01 && e->data_len > 0) on_host_text((esp_websocket_client_handle_t)arg, e->data_ptr, e->data_len);
        else if (e->op_code == 0x02) on_host_binary((const uint8_t *)e->data_ptr, e->data_len);
        else if (e->op_code == 0x08 && e->data_len >= 2) ESP_LOGI(TAG, "host closed the socket with code %d", ((uint8_t)e->data_ptr[0] << 8) | (uint8_t)e->data_ptr[1]);
        break;
    case WEBSOCKET_EVENT_ERROR:
        ESP_LOGE(TAG, "wss error (host running? address, port, clock, ca.pem)");
        break;
    default:
        break;
    }
}

void app_main(void)
{
    g_mu = xSemaphoreCreateMutex();
    g_ring = xRingbufferCreate(RING_BYTES, RINGBUF_TYPE_BYTEBUF);
    if (!g_ring) { ESP_LOGE(TAG, "no memory for the audio buffer"); return; }
    i2c_master_bus_handle_t bus = NULL;
    i2c_master_bus_config_t ib = {.i2c_port = I2C_NUM_0, .sda_io_num = NT_I2C_SDA, .scl_io_num = NT_I2C_SCL, .clk_source = I2C_CLK_SRC_DEFAULT, .glitch_ignore_cnt = 7, .flags.enable_internal_pullup = true};
    ESP_ERROR_CHECK(i2c_new_master_bus(&ib, &bus));
    audio_init(bus);
    gpio_config_t bc = {.mode = GPIO_MODE_INPUT, .pull_up_en = GPIO_PULLUP_ENABLE, .pin_bit_mask = (1ULL << NT_BTN_PLUS) | (1ULL << NT_BTN_BOOT)};
    ESP_ERROR_CHECK(gpio_config(&bc));
    xTaskCreate(playback_task, "playback", 6144, NULL, 6, NULL);
    xTaskCreate(capture_task, "capture", 8192, NULL, 6, NULL);

    ESP_ERROR_CHECK(nvs_flash_init());
    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());
    esp_netif_create_default_wifi_sta();
    s_ev = xEventGroupCreate();
    wifi_init_config_t wc = WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&wc));
    ESP_ERROR_CHECK(esp_event_handler_register(WIFI_EVENT, ESP_EVENT_ANY_ID, wifi_event, NULL));
    ESP_ERROR_CHECK(esp_event_handler_register(IP_EVENT, IP_EVENT_STA_GOT_IP, wifi_event, NULL));
    wifi_config_t cfg = {0};
    strlcpy((char *)cfg.sta.ssid, CONFIG_NT_WIFI_SSID, sizeof(cfg.sta.ssid));
    strlcpy((char *)cfg.sta.password, CONFIG_NT_WIFI_PASSWORD, sizeof(cfg.sta.password));
    cfg.sta.threshold.authmode = WIFI_AUTH_WPA2_PSK;
    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
    ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_STA, &cfg));
    ESP_ERROR_CHECK(esp_wifi_start());
    xEventGroupWaitBits(s_ev, GOT_IP, pdFALSE, pdTRUE, portMAX_DELAY);
    esp_sntp_config_t sntp = ESP_NETIF_SNTP_DEFAULT_CONFIG("pool.ntp.org");
    ESP_ERROR_CHECK(esp_netif_sntp_init(&sntp));
    if (esp_netif_sntp_sync_wait(pdMS_TO_TICKS(20000)) != ESP_OK) ESP_LOGE(TAG, "clock not synced in 20 s; the certificate check will fail");

    char uri[64];
    snprintf(uri, sizeof(uri), "wss://%s:%d", CONFIG_NT_HOST_IP, CONFIG_NT_HOST_PORT);
    esp_websocket_client_config_t wsc = {.uri = uri, .cert_pem = ca_pem_start, .reconnect_timeout_ms = 5000, .network_timeout_ms = 10000, .task_stack = 12288, .buffer_size = 8192};
    g_ws = esp_websocket_client_init(&wsc);
    esp_websocket_register_events(g_ws, WEBSOCKET_EVENT_ANY, ws_event, (void *)g_ws);
    ESP_ERROR_CHECK(esp_websocket_client_start(g_ws));
    ESP_LOGI(TAG, "ready. Microphone is MUTED. +/KEY unmutes or mutes the microphone (only the physical button can unmute). BOOT/- is push-to-talk.");

    bool prev_plus = false, prev_main = false;
    for (;;) {
        bool plus = gpio_get_level(NT_BTN_PLUS) == 0, main_btn = gpio_get_level(NT_BTN_BOOT) == 0;
        if (plus && !prev_plus) {
            g_mic_enabled = g_mic_hw_ok && !g_mic_enabled;
            ESP_LOGI(TAG, "MICROPHONE %s", g_mic_enabled ? "ON (unmuted by the button)" : "OFF (muted)");
        }
        if (main_btn && !prev_main) {   // BOOT/- is the push-to-talk button, named "main" in the protocol
            ESP_LOGI(TAG, "button main pressed (push to talk)");
            send_signed(g_ws, "button.pressed", "btn", "{\"button\":\"main\",\"kind\":\"single\"}");
        }
        prev_plus = plus;
        prev_main = main_btn;
        vTaskDelay(pdMS_TO_TICKS(30));
    }
}
