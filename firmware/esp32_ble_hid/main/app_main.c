/*
 * ESP32-S3 HID bridge speaking the CH9329 serial protocol, with a Bluetooth LE or USB output.
 *
 *   host --UART0 (CH9329 frames)--> uart_rx task --timestamped chunks--> bridge task
 *        bridge task: ch9329 core (parse, dispatch, config) --> hid_link_send() --> BLE or USB --> iPhone
 *                                                           \--> reply frame --> UART0 --> host
 *
 * The reply to a HID command is written only after hid_link_send() returned: 00 means the report
 * was delivered to the phone's HID link (hid_link.h defines it per transport); E6 means it was not.
 */
#include <stdio.h>
#include <string.h>

#include "bridge_config.h"
#include "ch9329_proto.h"
#include "driver/gpio.h"
#include "esp_log.h"
#include "esp_mac.h"
#include "esp_system.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "hid_link.h"
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
    return hid_link_send(HID_COLL_KEYBOARD, report, CH9329_KB_REPORT_LEN);
}

static uint8_t sink_mouse(void *ctx, const uint8_t report[CH9329_MOUSE_REPORT_LEN])
{
    (void)ctx;
    return hid_link_send(HID_COLL_MOUSE, report, CH9329_MOUSE_REPORT_LEN);
}

static uint8_t sink_consumer(void *ctx, const uint8_t report[CH9329_CONSUMER_REPORT_LEN])
{
    (void)ctx;
    return hid_link_send(HID_COLL_CONSUMER, report, CH9329_CONSUMER_REPORT_LEN);
}

static uint8_t sink_system(void *ctx, const uint8_t report[CH9329_SYSTEM_REPORT_LEN])
{
    (void)ctx;
    return hid_link_send(HID_COLL_SYSTEM, report, CH9329_SYSTEM_REPORT_LEN);
}

static uint8_t sink_abs(void *ctx, const uint8_t report[CH9329_ABS_REPORT_LEN])
{
    (void)ctx;
    return hid_link_send(HID_COLL_ABS_POINTER, report, CH9329_ABS_REPORT_LEN);
}

static bool sink_link_ready(void *ctx)
{
    (void)ctx;
    return hid_link_ready();
}

static uint8_t sink_leds(void *ctx)
{
    (void)ctx;
    return hid_link_leds();
}

static uint16_t sink_report_period(void *ctx)
{
    (void)ctx;
    return hid_link_report_period();
}

/* SEND_MS_REL_RUN pacing (bridge task): 1 ms FreeRTOS ticks, esp_timer milliseconds. */
static uint32_t sink_clock(void *ctx)
{
    (void)ctx;
    return transport_now_ms();
}

static void sink_sleep_until(void *ctx, uint32_t t_ms)
{
    (void)ctx;
    const int32_t wait = (int32_t)(t_ms - transport_now_ms());
    if (wait > 1) {
        vTaskDelay(pdMS_TO_TICKS((uint32_t)(wait - 1))); /* may wake up to 1 tick early... */
    }
    while ((int32_t)(t_ms - transport_now_ms()) > 0) {
        /* ...so finish on the clock (< 1 ms; the higher-priority UART reader still preempts). */
    }
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
    hid_link_shutdown(500);           /* the phone sees a clean disconnect / detach */
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
    transport_stats_t u;
    transport_uart_stats(&u);
    const ch9329_core_stats_t *c = &s_core.stats;           /* approximate: owned by bridge task */
    const ch9329_parser_stats_t *p = &s_core.parser.stats;
    ESP_LOGI(TAG,
             "link %s | frames %lu bad_sum %lu timeouts %lu | hid ok %lu fail %lu runs %lu | uart bytes %lu "
             "ovf %lu drops %lu lineerr %lu gaps %lu",
             hid_link_ready() ? "ready" : "down", (unsigned long)p->frames, (unsigned long)p->bad_sum,
             (unsigned long)p->timeouts, (unsigned long)c->hid_sent, (unsigned long)c->hid_failed,
             (unsigned long)c->runs, (unsigned long)u.bytes, (unsigned long)u.uart_overflows,
             (unsigned long)u.queue_drops, (unsigned long)u.line_errors, (unsigned long)s_discontinuities);
    hid_link_log_stats();
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
                ESP_LOGW(TAG, "pairing button held 3 s: forgetting paired phones");
                hid_link_forget_peers();
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
        .abs_mouse_report = sink_abs,
        .link_ready = sink_link_ready,
        .leds = sink_leds,
        .report_period = sink_report_period,
        .persist_load = sink_load,
        .persist_store = sink_store,
        .request_restart = sink_restart,
        .clock_ms = sink_clock,
        .sleep_until_ms = sink_sleep_until,
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
    /* The configuration stored by SET_PARA_CFG is applied here, at boot (after RESET too). */
    ch9329_core_init(&s_core, &sink, &opt);
    const ch9329_persist_t *active = ch9329_core_active(&s_core);
    const hid_profile_t profile = ch9329_profile_for_work_mode(ch9329_cfg_work_mode(active->cfg));
    /* Pointer collections come from the build (Kconfig BRIDGE_POINTERS). */
    const unsigned collections = hid_profile_collections(profile, BRIDGE_POINTERS);
    if ((collections & HID_COLL_BIT(HID_COLL_ABS_POINTER)) == 0u) {
        /* No absolute pointer: SEND_MS_ABS_DATA follows CONFIG_BRIDGE_ABS_MOUSE_REJECT. */
        s_core.sink.abs_mouse_report = NULL;
    }
    const uint32_t baud = ch9329_cfg_baud(active->cfg);
    ch9329_core_set_rx_slack(&s_core, transport_uart_timing_slack_ms(baud));
    ch9329_core_set_link_info(&s_core, hid_link_output_id(), (uint8_t)collections);
    ESP_LOGI(TAG, "%s config: work mode 0x%02X (%s, collections 0x%02X), address 0x%02X, %lu baud, "
                  "packet interval %u ms (+%lu ms UART slack)",
             s_core.used_defaults ? "default" : "stored", ch9329_cfg_work_mode(active->cfg), hid_profile_name(profile),
             collections,
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
    /* VID/PID come from Kconfig, one PID per profile; the CH9329 block's VID/PID bytes are stored
     * and read back but not used (see README). */
    const uint16_t pids[HID_PROFILE_COUNT] = {
        [HID_PROFILE_COMPOSITE] = CONFIG_BRIDGE_PID_COMPOSITE,
        [HID_PROFILE_KEYBOARD] = CONFIG_BRIDGE_PID_KEYBOARD,
        [HID_PROFILE_MOUSE] = CONFIG_BRIDGE_PID_MOUSE,
    };
    const hid_link_config_t link = {
        .profile = profile,
        .collections = collections,
        .name = name,
        .manufacturer = active->str[CH9329_STR_VENDOR].text,
        .serial = active->str[CH9329_STR_SERIAL].len > 0u ? active->str[CH9329_STR_SERIAL].text : serial,
        .vid = CONFIG_BRIDGE_VID,
        .pid = pids[profile],
    };
    ESP_ERROR_CHECK(hid_link_start(&link));
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
