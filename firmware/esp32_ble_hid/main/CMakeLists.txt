idf_component_register(SRCS "app_main.c" "ble_hid.c" "transport_uart.c" "persist_nvs.c"
                       INCLUDE_DIRS "."
                       REQUIRES ch9329_proto bt nvs_flash esp_driver_uart esp_driver_gpio esp_timer
                                esp_ringbuf esp_app_format esp_system freertos log)

target_compile_options(${COMPONENT_LIB} PRIVATE -Werror -Wshadow -Wsign-compare -Wunused-parameter)
