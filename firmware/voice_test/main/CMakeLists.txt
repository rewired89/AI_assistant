idf_component_register(SRCS "main.c" "face.c" INCLUDE_DIRS "." "../../hardware"
                       REQUIRES esp_http_server esp_lcd esp_timer esp_wifi esp_netif esp_event mbedtls json nvs_flash driver esp_driver_i2s esp_driver_i2c
                                espressif__esp_websocket_client espressif__esp_codec_dev
                       EMBED_TXTFILES "ca.pem")
