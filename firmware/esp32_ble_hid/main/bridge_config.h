/*
 * Task layout and compile-time checks of the Kconfig values (main/Kconfig.projbuild).
 *
 *   core 0: NimBLE host + controller (BLE build) or the TinyUSB task (USB build)
 *   core 1: uart_rx (reader, prio 12) > bridge (protocol core, prio 10) > housekeeping (prio 2)
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

/* Pointer collections of the build (hid_report_map.h hid_pointers_t). */
#if CONFIG_BRIDGE_POINTERS_REL_ONLY
#define BRIDGE_POINTERS HID_POINTERS_REL_ONLY
#elif CONFIG_BRIDGE_POINTERS_ABS_ONLY
#define BRIDGE_POINTERS HID_POINTERS_ABS_ONLY
#else
#define BRIDGE_POINTERS HID_POINTERS_REL_AND_ABS
#endif

#define BRIDGE_UART_RX_TASK_PRIO 12
#define BRIDGE_TASK_PRIO 10
#define BRIDGE_BUTTON_TASK_PRIO 2

#if CONFIG_FREERTOS_HZ != 1000
#error "CONFIG_FREERTOS_HZ must be 1000: link waits, REL_RUN pacing and UART gap timing use 1 ms ticks"
#endif

#if CONFIG_BRIDGE_OUTPUT_BLE
#if !CONFIG_BT_ENABLED || !CONFIG_BT_NIMBLE_ENABLED
#error "BRIDGE_OUTPUT_BLE needs CONFIG_BT_ENABLED and CONFIG_BT_NIMBLE_ENABLED (sdkconfig.defaults)"
#endif
#if CONFIG_BRIDGE_CONN_ITVL_MAX < CONFIG_BRIDGE_CONN_ITVL_MIN
#error "CONFIG_BRIDGE_CONN_ITVL_MAX must be >= CONFIG_BRIDGE_CONN_ITVL_MIN"
#endif
/* Bluetooth Core: supervision timeout > (1 + latency) * interval_max * 2. */
#if (CONFIG_BRIDGE_CONN_SUPERVISION_TIMEOUT_MS * 4) <= \
    ((1 + CONFIG_BRIDGE_CONN_LATENCY) * CONFIG_BRIDGE_CONN_ITVL_MAX * 5 * 2)
#error "CONFIG_BRIDGE_CONN_SUPERVISION_TIMEOUT_MS too short for the interval and latency"
#endif
#endif /* CONFIG_BRIDGE_OUTPUT_BLE */

#if CONFIG_BRIDGE_NOTIFY_WAIT_MS >= 450
#error "CONFIG_BRIDGE_NOTIFY_WAIT_MS must stay well below the host's 500 ms reply timeout"
#endif
