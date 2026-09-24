/*
 * The HID link to the phone: one interface, two implementations chosen at build time
 * (Kconfig BRIDGE_OUTPUT):
 *
 *   ble_hid.c  Bluetooth LE HID over GATT (NimBLE): Lightning iPhones, whose only port is taken
 *              by the HDMI adapter.
 *   usb_hid.c  USB HID device on the ESP32-S3's native USB port (TinyUSB): for a phone/hub that
 *              accepts a USB keyboard and mouse, as an alternative to the CH9329 cable.
 *
 * Contract of hid_link_send() (both implementations): CH9329_STATUS_OK only when the report was
 * delivered to the phone's HID link as defined for that transport, otherwise
 * CH9329_STATUS_EXEC_FAILED. Reports go out in call order. Blocking, bounded: call it from the
 * bridge task only.
 *
 *   BLE: the notification was accepted by NimBLE (ble_gatts_notify_custom() == 0) on a connected,
 *        encrypted link whose central subscribed to that report. The link layer then
 *        retransmits it until acknowledged or the link drops.
 *   USB: tud_hid_n_report() accepted it AND tud_hid_report_complete_cb() confirmed the host read
 *        that very report from the endpoint (hid_confirm.h matches completions to reports),
 *        within CONFIG_BRIDGE_USB_CONFIRM_WAIT_MS.
 */
#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "ch9329_proto.h"
#include "esp_err.h"
#include "hid_report_map.h"

/* Longest name and serial number (without the NUL) hid_link_start() presents unshortened. */
#define HID_LINK_NAME_MAX 29u /* BLE: the whole Local Name fits in the scan response (31 - 2) */
/* A SET_USB_STRING serial number (23 characters) + "-" + the identity tag (hid_identity_tag()). */
#define HID_LINK_SERIAL_MAX (CH9329_STR_MAX + 1u + HID_IDENTITY_TAG_MAX)

typedef struct {
    hid_profile_t profile;
    unsigned collections;     /* HID_COLL_* bits the phone sees (never changes while running) */
    const char *name;         /* BLE device name / USB product string, identity tag included */
    const char *manufacturer; /* DIS / USB manufacturer string */
    const char *serial;       /* DIS / USB serial number string, identity tag included */
    uint16_t vid, pid;        /* BLE PnP ID / USB device descriptor */
} hid_link_config_t;

esp_err_t hid_link_start(const hid_link_config_t *cfg);

/* Deliver one input report of collection `which` (payload without report id). */
uint8_t hid_link_send(hid_coll_t which, const uint8_t *data, size_t len);

/* When the link ACCEPTED the last report hid_link_send() answered OK for, on the esp_timer
 * microsecond clock truncated to 32 bits (SEND_MS_REL_RUN lateness, ch9329_sink_t.accepted_us).
 * BLE: NimBLE queued the notification. USB: the report was placed in the endpoint (the OK itself
 * waits until the phone has read it, up to one poll later). Bridge task only. */
uint32_t hid_link_accepted_us(void);

/* GET_INFO byte 1: every primary report of the profile can be delivered right now. */
bool hid_link_ready(void);

/* Keyboard LEDs written by the phone (bit0 Num, bit1 Caps, bit2 Scroll). */
uint8_t hid_link_leds(void);

/* GET_INFO bytes 6-7: how often the phone takes a report, in 0.25 ms units; 0 while not
 * connected. BLE: the current connection interval. USB: the IN endpoints' polling interval,
 * while configured. */
uint16_t hid_link_report_period(void);

/* Leave the phone cleanly before a restart (BLE: terminate the connection; USB: detach). */
void hid_link_shutdown(uint32_t timeout_ms);

/* BLE: delete every bond. USB: nothing to forget. Any task. */
void hid_link_forget_peers(void);

/* CH9329_OUTPUT_BLE or CH9329_OUTPUT_USB (GET_INFO byte 3). */
uint8_t hid_link_output_id(void);

/* One log line with the link's counters. */
void hid_link_log_stats(void);

/* Primary collections for hid_link_ready(): the keyboard if present, and the relative mouse if
 * present, else the absolute pointer. */
static inline unsigned hid_link_primary(unsigned collections)
{
    unsigned p = collections & HID_COLL_BIT(HID_COLL_KEYBOARD);
    if (collections & HID_COLL_BIT(HID_COLL_MOUSE)) {
        p |= HID_COLL_BIT(HID_COLL_MOUSE);
    } else {
        p |= collections & HID_COLL_BIT(HID_COLL_ABS_POINTER);
    }
    return p;
}
