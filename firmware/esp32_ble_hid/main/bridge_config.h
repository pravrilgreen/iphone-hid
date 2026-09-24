/*
 * Task layout and compile-time checks of the Kconfig values (main/Kconfig.projbuild).
 *
 *   core 0: NimBLE host + controller (ESP-IDF default, CONFIG_BT_NIMBLE_PINNED_TO_CORE_0)
 *   core 1: uart_rx (reader, prio 12) > bridge (protocol core, prio 10) > button (prio 2)
 *
 * uart_rx must outrank bridge on the same core: whenever the bridge task runs, every byte the
 * UART interrupt has delivered has already been stamped and queued (see transport_uart.h).
 */
#pragma once

#include "freertos/FreeRTOS.h"
#include "sdkconfig.h"

#if CONFIG_FREERTOS_UNICORE
#define BRIDGE_TASK_CORE 0
#else
#define BRIDGE_TASK_CORE 1
#endif

#define BRIDGE_UART_RX_TASK_PRIO 12
#define BRIDGE_TASK_PRIO 10
#define BRIDGE_BUTTON_TASK_PRIO 2

#if CONFIG_FREERTOS_HZ != 1000
#error "CONFIG_FREERTOS_HZ must be 1000: BLE buffer waits and UART gap timing work in 1 ms steps"
#endif

#if CONFIG_BRIDGE_CONN_ITVL_MAX < CONFIG_BRIDGE_CONN_ITVL_MIN
#error "CONFIG_BRIDGE_CONN_ITVL_MAX must be >= CONFIG_BRIDGE_CONN_ITVL_MIN"
#endif

/* Bluetooth Core: supervision timeout > (1 + latency) * interval_max * 2. */
#if (CONFIG_BRIDGE_CONN_SUPERVISION_TIMEOUT_MS * 4) <= \
    ((1 + CONFIG_BRIDGE_CONN_LATENCY) * CONFIG_BRIDGE_CONN_ITVL_MAX * 5 * 2)
#error "CONFIG_BRIDGE_CONN_SUPERVISION_TIMEOUT_MS too short for the interval and latency"
#endif

#if CONFIG_BRIDGE_NOTIFY_WAIT_MS >= 450
#error "CONFIG_BRIDGE_NOTIFY_WAIT_MS must stay well below the host's 500 ms reply timeout"
#endif
