set(requires ch9329_proto bt nvs_flash esp_driver_uart esp_driver_gpio esp_timer esp_ringbuf esp_app_format
             esp_system freertos log)

# esp_tinyusb normally comes from the IDF Component Manager (main/idf_component.yml), which adds
# it to this component's requirements by itself. A build with the component manager disabled
# (IDF_COMPONENT_MANAGER=0) that provides esp_tinyusb and tinyusb as local components through
# EXTRA_COMPONENT_DIRS names it here instead.
if("$ENV{IDF_COMPONENT_MANAGER}" STREQUAL "0")
    list(APPEND requires esp_tinyusb)
endif()

idf_component_register(SRCS "app_main.c" "ble_hid.c" "usb_hid.c" "transport_uart.c" "persist_nvs.c"
                       INCLUDE_DIRS "."
                       REQUIRES ${requires})

# IDF adds -Wno-sign-compare -Wno-unused-parameter; the bridge's own code is held to them.
target_compile_options(${COMPONENT_LIB} PRIVATE -Werror -Wshadow -Wsign-compare -Wunused-parameter)
