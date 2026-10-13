// Network test: join Wi-Fi, sync the clock, open wss:// to the companion host and verify its certificate against OUR private CA (main/ca.pem).
// The host closes the socket (code 4001/4003) if no valid signed first message arrives within 10 s: that close, after a successful handshake, is the expected result here.
// Wi-Fi name and password come from `idf.py menuconfig` (kept in the local, git-ignored sdkconfig). Nothing here is a secret except those two values.
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

static const char *TAG = "nt_net_test";
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
    send_signed(c, "device.ready", "hello", "{\"boot_cause\":\"power_on\",\"firmware_version\":\"nt-net-0.2\",\"protocol_versions\":[1],\"recovered_state\":false}");
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

static void on_host_text(esp_websocket_client_handle_t c, const char *txt, int n)
{
    cJSON *root = NULL;
    if (!verify_incoming(txt, n, &root)) {
        if (++s_dropped % 10 == 1) ESP_LOGW(TAG, "dropped an unauthentic or stale message (%d so far); no reply is sent", s_dropped);
        return;
    }
    cJSON *type = cJSON_GetObjectItem(root, "type"), *mid = cJSON_GetObjectItem(root, "message_id");
    if (!cJSON_IsString(type) || !cJSON_IsString(mid) || !safe_ident(mid->valuestring) || strlen(type->valuestring) > 40) {
        cJSON_Delete(root);
        return;
    }
    ESP_LOGI(TAG, "VERIFIED command %s (id %s)", type->valuestring, mid->valuestring);
    // This test firmware implements no commands yet, and says so honestly instead of pretending.
    char payload[240];
    snprintf(payload, sizeof payload, "{\"code\":\"unsupported_command\",\"command_id\":\"%s\",\"message\":\"not implemented in this test firmware\"}", mid->valuestring);
    send_signed(c, "command.failed", "fail", payload);
    cJSON_Delete(root);
}

static void ws_event(void *arg, esp_event_base_t base, int32_t id, void *data)
{
    esp_websocket_event_data_t *e = data;
    switch (id) {
    case WEBSOCKET_EVENT_CONNECTED:
        ESP_LOGI(TAG, "wss CONNECTED: TLS handshake and certificate check against our CA succeeded");
        send_hello((esp_websocket_client_handle_t)arg);
        break;
    case WEBSOCKET_EVENT_DISCONNECTED:
        ESP_LOGW(TAG, "wss disconnected");
        break;
    case WEBSOCKET_EVENT_DATA:
        if (e->op_code == 0x01 && e->data_len > 0) {
            if (e->payload_offset == 0 && e->data_len == e->payload_len) on_host_text((esp_websocket_client_handle_t)arg, e->data_ptr, e->data_len);
            else ESP_LOGW(TAG, "fragmented frame ignored (not expected for protocol messages)");
        }
        if (e->op_code == 0x08 && e->data_len >= 2) {
            int code = ((uint8_t)e->data_ptr[0] << 8) | (uint8_t)e->data_ptr[1];
            ESP_LOGI(TAG, "host closed the socket with code %d (4001/4003 = no signed first message, as expected in this test)", code);
        }
        break;
    case WEBSOCKET_EVENT_ERROR:
        ESP_LOGE(TAG, "wss error (check the host is running, the address, the port, the clock, and that ca.pem is the CA that signed the host certificate)");
        break;
    default:
        break;
    }
}

void app_main(void)
{
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
    cfg.sta.threshold.authmode = WIFI_AUTH_WPA2_PSK;  // never fall back to open or WEP
    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
    ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_STA, &cfg));
    ESP_ERROR_CHECK(esp_wifi_start());
    if (strlen(CONFIG_NT_WIFI_SSID) == 0) ESP_LOGE(TAG, "no Wi-Fi name set: run idf.py menuconfig, Nine Tails network test");
    ESP_LOGI(TAG, "joining Wi-Fi '%s'", CONFIG_NT_WIFI_SSID);
    xEventGroupWaitBits(s_ev, GOT_IP, pdFALSE, pdTRUE, portMAX_DELAY);

    // Certificates have validity dates, so the clock must be right before TLS. This test uses public NTP; the final design should take time from the host.
    esp_sntp_config_t sntp = ESP_NETIF_SNTP_DEFAULT_CONFIG("pool.ntp.org");
    ESP_ERROR_CHECK(esp_netif_sntp_init(&sntp));
    if (esp_netif_sntp_sync_wait(pdMS_TO_TICKS(20000)) != ESP_OK) ESP_LOGE(TAG, "clock not synced in 20 s; the certificate check will fail");
    else {
        time_t now = 0;
        time(&now);
        ESP_LOGI(TAG, "clock synced, unix time %lld", (long long)now);
    }

    char uri[64];
    snprintf(uri, sizeof(uri), "wss://%s:%d", CONFIG_NT_HOST_IP, CONFIG_NT_HOST_PORT);
    esp_websocket_client_config_t wsc = {.uri = uri, .cert_pem = ca_pem_start, .reconnect_timeout_ms = 5000, .network_timeout_ms = 10000, .task_stack = 10240, .buffer_size = 2048};
    ESP_LOGI(TAG, "connecting to %s", uri);
    esp_websocket_client_handle_t c = esp_websocket_client_init(&wsc);
    esp_websocket_register_events(c, WEBSOCKET_EVENT_ANY, ws_event, (void *)c);
    ESP_ERROR_CHECK(esp_websocket_client_start(c));
    for (;;) vTaskDelay(pdMS_TO_TICKS(10000));
}
