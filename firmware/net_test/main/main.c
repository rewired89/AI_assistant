// Network test: join Wi-Fi, sync the clock, open wss:// to the companion host and verify its certificate against OUR private CA (main/ca.pem).
// The host closes the socket (code 4001/4003) if no valid signed first message arrives within 10 s: that close, after a successful handshake, is the expected result here.
// Wi-Fi name and password come from `idf.py menuconfig` (kept in the local, git-ignored sdkconfig). Nothing here is a secret except those two values.
#include <string.h>
#include "esp_event.h"
#include "esp_log.h"
#include "esp_netif.h"
#include "esp_netif_sntp.h"
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

static void ws_event(void *arg, esp_event_base_t base, int32_t id, void *data)
{
    esp_websocket_event_data_t *e = data;
    switch (id) {
    case WEBSOCKET_EVENT_CONNECTED:
        ESP_LOGI(TAG, "wss CONNECTED: TLS handshake and certificate check against our CA succeeded");
        break;
    case WEBSOCKET_EVENT_DISCONNECTED:
        ESP_LOGW(TAG, "wss disconnected");
        break;
    case WEBSOCKET_EVENT_DATA:
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
    esp_websocket_client_config_t wsc = {.uri = uri, .cert_pem = ca_pem_start, .reconnect_timeout_ms = 5000, .network_timeout_ms = 10000};
    ESP_LOGI(TAG, "connecting to %s", uri);
    esp_websocket_client_handle_t c = esp_websocket_client_init(&wsc);
    esp_websocket_register_events(c, WEBSOCKET_EVENT_ANY, ws_event, NULL);
    ESP_ERROR_CHECK(esp_websocket_client_start(c));
    for (;;) vTaskDelay(pdMS_TO_TICKS(10000));
}
