/*
 * ESP32-S3 BLE HID bridge speaking the CH9329 serial protocol.
 *
 *   host --UART0 (CH9329 frames)--> uart_rx task --timestamped chunks--> bridge task
 *        bridge task: ch9329 core (parse, dispatch, config) --> ble_hid_send() --> NimBLE --> iPhone
 *                                                           \--> reply frame --> UART0 --> host
 *
 * The reply to a HID command is written only after ble_hid_send() returned: 00 means the report
 * was queued as a notification to the connected, subscribed iPhone; E6 means it was not.
 */
#include <stdio.h>
#include <string.h>

#include "ble_hid.h"
#include "bridge_config.h"
#include "ch9329_proto.h"
#include "driver/gpio.h"
#include "esp_log.h"
#include "esp_mac.h"
#include "esp_system.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "hid_report_map.h"
#include "persist_nvs.h"
#include "transport_uart.h"

static const char *TAG = "bridge";

static ch9329_core_t s_core; /* bridge task only (after app_main hands it over) */
static volatile bool s_restart;
static uint32_t s_discontinuities;

/* ---- sink: what the protocol core calls (always from the bridge task) --------------------- */

static void sink_reply(void *ctx, const uint8_t *frame, size_t len)
{
    (void)ctx;
    transport_uart_write(frame, len);
}

static uint8_t sink_keyboard(void *ctx, const uint8_t report[CH9329_KB_REPORT_LEN])
{
    (void)ctx;
    return ble_hid_send(BLE_HID_IN_KEYBOARD, report, CH9329_KB_REPORT_LEN);
}

static uint8_t sink_mouse(void *ctx, const uint8_t report[CH9329_MOUSE_REPORT_LEN])
{
    (void)ctx;
    return ble_hid_send(BLE_HID_IN_MOUSE, report, CH9329_MOUSE_REPORT_LEN);
}

static uint8_t sink_consumer(void *ctx, const uint8_t report[CH9329_CONSUMER_REPORT_LEN])
{
    (void)ctx;
    return ble_hid_send(BLE_HID_IN_CONSUMER, report, CH9329_CONSUMER_REPORT_LEN);
}

static uint8_t sink_system(void *ctx, const uint8_t report[CH9329_SYSTEM_REPORT_LEN])
{
    (void)ctx;
    return ble_hid_send(BLE_HID_IN_SYSTEM, report, CH9329_SYSTEM_REPORT_LEN);
}

static bool sink_link_ready(void *ctx)
{
    (void)ctx;
    return ble_hid_link_ready();
}

static uint8_t sink_leds(void *ctx)
{
    (void)ctx;
    return ble_hid_leds();
}

static bool sink_load(void *ctx, ch9329_persist_t *out)
{
    (void)ctx;
    return persist_load(out);
}

static bool sink_store(void *ctx, const ch9329_persist_t *p)
{
    (void)ctx;
    return persist_store(p);
}

static void sink_restart(void *ctx)
{
    (void)ctx;
    s_restart = true; /* acted on once the current chunk is processed (the reply is queued) */
}

/* ---- tasks -------------------------------------------------------------------------------- */

static void restart_now(void)
{
    ESP_LOGI(TAG, "RESET: restarting (the stored configuration becomes active)");
    transport_uart_wait_tx_done(200); /* the RESET reply leaves the wire first */
    ble_hid_disconnect(500);          /* the iPhone sees a clean disconnect, not a 6 s timeout */
    esp_restart();
}

static void bridge_task(void *arg)
{
    (void)arg;
    for (;;) {
        /* Sleep until the next chunk, or until a pending partial frame would expire. */
        const uint32_t wait = ch9329_core_ms_until_timeout(&s_core, transport_now_ms());
        uart_chunk_t chunk;
        if (transport_uart_receive(&chunk, wait)) {
            if (chunk.discontinuity) {
                /* Bytes were lost in between: a frame spanning the hole must not be completed
                 * with unrelated bytes. It gets no reply; the host's 500 ms timeout reports it. */
                ch9329_parser_reset(&s_core.parser);
                s_discontinuities++;
            }
            /* Arrival time, not now: time spent waiting for BLE buffers is not a line gap. */
            ch9329_core_feed(&s_core, chunk.data, chunk.len, chunk.t_ms);
            transport_uart_release(&chunk);
        } else {
            /* Nothing queued: every byte that has arrived was already fed, so "now" is safe. */
            ch9329_core_poll(&s_core, transport_now_ms());
        }
        if (s_restart) {
            restart_now();
        }
    }
}

static void log_stats(void)
{
    ble_hid_stats_t b;
    transport_stats_t u;
    ble_hid_stats(&b);
    transport_uart_stats(&u);
    const ch9329_core_stats_t *c = &s_core.stats;           /* approximate: owned by bridge task */
    const ch9329_parser_stats_t *p = &s_core.parser.stats;
    ESP_LOGI(TAG,
             "link %s | frames %lu bad_sum %lu timeouts %lu | hid ok %lu fail %lu | notify ok %lu refused %lu "
             "timeout %lu err %lu waits %lu | uart bytes %lu ovf %lu drops %lu lineerr %lu gaps %lu",
             ble_hid_link_ready() ? "ready" : "down", (unsigned long)p->frames, (unsigned long)p->bad_sum,
             (unsigned long)p->timeouts, (unsigned long)c->hid_sent, (unsigned long)c->hid_failed,
             (unsigned long)b.notify_ok, (unsigned long)b.notify_refused, (unsigned long)b.notify_timeout,
             (unsigned long)b.notify_error, (unsigned long)b.buffer_waits, (unsigned long)u.bytes,
             (unsigned long)u.uart_overflows, (unsigned long)u.queue_drops, (unsigned long)u.line_errors,
             (unsigned long)s_discontinuities);
}

/* BOOT button held 3 s: delete all bonds. Also logs counters once a minute (USB Serial/JTAG). */
static void housekeeping_task(void *arg)
{
    (void)arg;
#if CONFIG_BRIDGE_PAIR_BUTTON_GPIO >= 0
    const gpio_config_t io = {
        .pin_bit_mask = 1ULL << CONFIG_BRIDGE_PAIR_BUTTON_GPIO,
        .mode = GPIO_MODE_INPUT,
        .pull_up_en = GPIO_PULLUP_ENABLE,
        .pull_down_en = GPIO_PULLDOWN_DISABLE,
        .intr_type = GPIO_INTR_DISABLE,
    };
    ESP_ERROR_CHECK(gpio_config(&io));
    uint32_t held_ms = 0;
#endif
    uint32_t ticks = 0;
    for (;;) {
        vTaskDelay(pdMS_TO_TICKS(50));
#if CONFIG_BRIDGE_PAIR_BUTTON_GPIO >= 0
        if (gpio_get_level((gpio_num_t)CONFIG_BRIDGE_PAIR_BUTTON_GPIO) == 0) {
            held_ms += 50;
            if (held_ms == 3000) {
                ESP_LOGW(TAG, "pairing button held 3 s: deleting all bonds");
                ble_hid_clear_bonds();
            }
        } else {
            held_ms = 0;
        }
#endif
        if (++ticks % 1200u == 0u) {
            log_stats();
        }
    }
}

/* ---- boot --------------------------------------------------------------------------------- */

void app_main(void)
{
    persist_init();

    const ch9329_sink_t sink = {
        .ctx = NULL,
        .send_reply = sink_reply,
        .keyboard_report = sink_keyboard,
        .mouse_report = sink_mouse,
        .consumer_report = sink_consumer,
        .system_report = sink_system,
        .link_ready = sink_link_ready,
        .leds = sink_leds,
        .persist_load = sink_load,
        .persist_store = sink_store,
        .request_restart = sink_restart,
    };
#ifdef CONFIG_BRIDGE_ABS_MOUSE_REJECT
    const bool abs_reject = true;
#else
    const bool abs_reject = false;
#endif
    const ch9329_options_t opt = {
        .abs_mouse_reject = abs_reject,
        .rx_slack_ms = 0, /* set below, once the baud rate is known */
    };
    ch9329_core_init(&s_core, &sink, &opt);

    /* The configuration stored by SET_PARA_CFG is applied here, at boot, like the real chip. */
    const ch9329_persist_t *active = ch9329_core_active(&s_core);
    const uint32_t baud = ch9329_cfg_baud(active->cfg);
    ch9329_core_set_rx_slack(&s_core, transport_uart_timing_slack_ms(baud));
    const hid_profile_t profile = ch9329_profile_for_work_mode(ch9329_cfg_work_mode(active->cfg));
    ESP_LOGI(TAG, "%s config: work mode 0x%02X (%s), address 0x%02X, %lu baud, packet interval %u ms "
                  "(+%lu ms UART slack)",
             s_core.used_defaults ? "default" : "stored", ch9329_cfg_work_mode(active->cfg), hid_profile_name(profile),
             ch9329_cfg_address(active->cfg), (unsigned long)baud,
             (unsigned)ch9329_cfg_u16(active->cfg, CH9329_CFG_OFF_PACKET_INTERVAL),
             (unsigned long)transport_uart_timing_slack_ms(baud));

    uint8_t mac[6] = {0};
    esp_read_mac(mac, ESP_MAC_BT);
    char name[32];
    char serial[16];
    if (active->str[CH9329_STR_PRODUCT].len > 0u) {
        snprintf(name, sizeof(name), "%s", active->str[CH9329_STR_PRODUCT].text);
    } else {
        snprintf(name, sizeof(name), "%s %02X%02X", CONFIG_BRIDGE_DEVICE_NAME_PREFIX, mac[4], mac[5]);
    }
    snprintf(serial, sizeof(serial), "%02X%02X%02X%02X%02X%02X", mac[0], mac[1], mac[2], mac[3], mac[4], mac[5]);
    const ble_hid_config_t ble = {
        .profile = profile,
        .name = name,
        .manufacturer = active->str[CH9329_STR_VENDOR].text,
        .serial = active->str[CH9329_STR_SERIAL].len > 0u ? active->str[CH9329_STR_SERIAL].text : serial,
        .vid = ch9329_cfg_u16(active->cfg, CH9329_CFG_OFF_VID),
        .pid = ch9329_cfg_u16(active->cfg, CH9329_CFG_OFF_PID),
    };
    ESP_ERROR_CHECK(ble_hid_start(&ble));
    ESP_ERROR_CHECK(transport_uart_start(baud));

    if (xTaskCreatePinnedToCore(bridge_task, "bridge", 6144, NULL, BRIDGE_TASK_PRIO, NULL, BRIDGE_TASK_CORE) !=
        pdPASS) {
        ESP_LOGE(TAG, "cannot start the bridge task");
        esp_restart();
    }
    if (xTaskCreatePinnedToCore(housekeeping_task, "housekeeping", 3072, NULL, BRIDGE_BUTTON_TASK_PRIO, NULL,
                                BRIDGE_TASK_CORE) != pdPASS) {
        ESP_LOGW(TAG, "cannot start the housekeeping task (no pairing button, no stats)");
    }
}
