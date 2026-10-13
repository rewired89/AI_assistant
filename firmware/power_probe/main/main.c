// Power probe: is a battery connected? Prints the battery-sense ADC (GPIO1, the vendor demo multiplies the pin voltage by 3) and the charging flag (GPIO3) once a second.
// Drives GPIO2 high exactly like the vendor demo does at start (its "battery enable"); never drives it low. Meanings of all three pins are from vendor source and UNVERIFIED.
// Read: a lithium cell shows roughly 3.0 to 4.2 V here. About 0 V or a wandering value means no battery is connected. Run it twice: with USB, and note any change when you plug/unplug the cable.
#include "board_waveshare_esp32s3_lcd154.h"
#include "driver/gpio.h"
#include "esp_adc/adc_cali.h"
#include "esp_adc/adc_cali_scheme.h"
#include "esp_adc/adc_oneshot.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

static const char *TAG = "nt_power_probe";

void app_main(void)
{
    gpio_config_t out = {.mode = GPIO_MODE_OUTPUT, .pin_bit_mask = 1ULL << NT_BAT_POWER};
    ESP_ERROR_CHECK(gpio_config(&out));
    gpio_set_level(NT_BAT_POWER, 1);
    gpio_config_t in = {.mode = GPIO_MODE_INPUT, .pull_up_en = GPIO_PULLUP_ENABLE, .pin_bit_mask = 1ULL << NT_CHARGING};
    ESP_ERROR_CHECK(gpio_config(&in));

    adc_oneshot_unit_handle_t adc = NULL;
    adc_oneshot_unit_init_cfg_t uc = {.unit_id = ADC_UNIT_1};
    ESP_ERROR_CHECK(adc_oneshot_new_unit(&uc, &adc));
    adc_oneshot_chan_cfg_t cc = {.bitwidth = ADC_BITWIDTH_DEFAULT, .atten = ADC_ATTEN_DB_12};
    ESP_ERROR_CHECK(adc_oneshot_config_channel(adc, ADC_CHANNEL_0, &cc));  // GPIO1 = ADC1 channel 0 on the ESP32-S3
    adc_cali_handle_t cali = NULL;
    adc_cali_curve_fitting_config_t cf = {.unit_id = ADC_UNIT_1, .chan = ADC_CHANNEL_0, .atten = ADC_ATTEN_DB_12, .bitwidth = ADC_BITWIDTH_DEFAULT};
    bool calibrated = adc_cali_create_scheme_curve_fitting(&cf, &cali) == ESP_OK;
    ESP_LOGI(TAG, "calibration %s; GPIO2 held high like the vendor demo", calibrated ? "available" : "NOT available (voltages are rough)");

    for (;;) {
        int raw = 0, mv = 0;
        ESP_ERROR_CHECK(adc_oneshot_read(adc, ADC_CHANNEL_0, &raw));
        if (calibrated) adc_cali_raw_to_voltage(cali, raw, &mv);
        else mv = raw * 3100 / 4095;
        ESP_LOGI(TAG, "ADC raw %4d, pin %4d mV, battery estimate x3 = %.2f V, charging flag (GPIO3) = %d", raw, mv, mv * 3 / 1000.0, gpio_get_level(NT_CHARGING));
        vTaskDelay(pdMS_TO_TICKS(1000));
    }
}
