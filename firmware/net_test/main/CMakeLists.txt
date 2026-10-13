idf_component_register(SRCS "main.c" INCLUDE_DIRS "."
                       REQUIRES esp_wifi esp_netif esp_event mbedtls nvs_flash espressif__esp_websocket_client
                       EMBED_TXTFILES "ca.pem")
