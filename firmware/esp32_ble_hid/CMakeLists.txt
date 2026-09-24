# ESP32-S3 BLE HID bridge speaking the CH9329 serial protocol. See README.md.
cmake_minimum_required(VERSION 3.16)

set(PROJECT_VER "1.0.0")

include($ENV{IDF_PATH}/tools/cmake/project.cmake)
project(esp32_ble_hid)
