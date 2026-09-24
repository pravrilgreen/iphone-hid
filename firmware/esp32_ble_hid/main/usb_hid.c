/*
 * USB HID device on the ESP32-S3's native USB port (TinyUSB through esp_tinyusb): the hid_link
 * implementation for CONFIG_BRIDGE_OUTPUT_USB (see hid_link.h for the delivery contract).
 *
 * USB layout: one configuration, one HID interface per group of collections, each with its own
 * interrupt IN endpoint polled every 1 ms. Keyboard LEDs arrive as SET_REPORT(Output) on the
 * control endpoint.
 *
 *   interface  collections           report ids  boot protocol
 *   keyboard   KEYBOARD              none        keyboard
 *   mouse      MOUSE (relative)      none        mouse
 *   pointer    ABS_POINTER           none        none
 *   extras     CONSUMER + SYSTEM     3, 4        none
 *
 * Only the interfaces whose collections are in the profile exist (keyboard-only: just the
 * keyboard), and each profile has its own PID (Kconfig), so the phone never pairs a cached
 * descriptor with another profile's interfaces. A separate absolute-pointer interface without a
 * report id is the layout PiKVM and Aiden use with iOS (studied for understanding only).
 *
 * Delivery: a report is answered 00 only after tud_hid_report_complete_cb() reported that the
 * host read it from the endpoint. Reports are serialised: the next one is queued only after the
 * previous one completed, so they reach the phone in the host's order.
 *
 * API usage follows ESP-IDF's examples/peripherals/usb/device/tusb_hid (Apache-2.0 / CC0) and
 * the TinyUSB headers; no example code was copied.
 */
#include "sdkconfig.h"

#if CONFIG_BRIDGE_OUTPUT_USB

#include <stdio.h>
#include <string.h>

#include "bridge_config.h"
#include "class/hid/hid_device.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "freertos/task.h"
#include "hid_link.h"
#include "tinyusb.h"

#if CONFIG_TINYUSB_HID_COUNT < 4
#error "CONFIG_TINYUSB_HID_COUNT must be 4 (see sdkconfig.defaults.usb)"
#endif

static const char *TAG = "usb_hid";

#define MAX_ITF 4
#define EP_SIZE 16    /* >= largest report + id byte (extras: 1 + 3) */
#define POLL_MS 1     /* bInterval: the phone polls each endpoint every 1 ms */
#define MAX_POWER_MA 100
#define MAX_IN_LEN 8u /* largest input payload (keyboard) */

typedef struct {
    unsigned colls;   /* HID_COLL_* bits carried by this interface */
    bool with_ids;
    uint8_t protocol; /* HID_ITF_PROTOCOL_* (boot keyboard / boot mouse / none) */
    const char *name;
    uint8_t desc[HID_DESC_MAX];
    uint16_t desc_len;
} itf_t;

/* Set in hid_link_start() before TinyUSB starts, read-only afterwards. */
static itf_t s_itf[MAX_ITF];
static uint8_t s_itf_count;
static int8_t s_coll_itf[HID_COLL_COUNT]; /* collection -> HID instance, -1 if absent */
static unsigned s_colls;
static unsigned s_primary;
static SemaphoreHandle_t s_done[MAX_ITF];
static tusb_desc_device_t s_dev_desc;
static uint8_t s_cfg_desc[TUD_CONFIG_DESC_LEN + MAX_ITF * TUD_HID_DESC_LEN];
static char s_manufacturer[32];
static char s_product[32];
static char s_serial[32];
static const char LANG_EN_US[2] = {0x09, 0x04};
static const char *s_strings[4];

/* Shared between the TinyUSB task (callbacks) and the bridge task. */
static portMUX_TYPE s_lock = portMUX_INITIALIZER_UNLOCKED;
static uint8_t s_leds;
static bool s_stalled; /* a report was not confirmed in time: link presumed stuck until it drains */
static uint8_t s_last[HID_COLL_COUNT][MAX_IN_LEN]; /* GET_REPORT answers */
typedef struct {
    uint32_t sent;        /* confirmed by the host */
    uint32_t refused;     /* not mounted / not in profile */
    uint32_t busy;        /* endpoint stayed busy (or bus suspended) for the whole wait */
    uint32_t rejected;    /* tud_hid_n_report() returned false */
    uint32_t unconfirmed; /* accepted, but the host did not read it within the confirm wait */
    uint32_t wakeups;     /* remote wakeup requests */
    uint32_t mounts;
} usb_stats_t;
static usb_stats_t s_stats;

/* ---- descriptors -------------------------------------------------------------------------- */

static void add_itf(unsigned colls, bool with_ids, uint8_t protocol, const char *name)
{
    colls &= s_colls;
    if (colls == 0u || s_itf_count >= MAX_ITF) {
        return;
    }
    itf_t *itf = &s_itf[s_itf_count];
    itf->colls = colls;
    itf->with_ids = with_ids;
    itf->protocol = protocol;
    itf->name = name;
    const size_t n = hid_desc_build(itf->desc, sizeof(itf->desc), colls, with_ids);
    if (n == 0u) {
        ESP_LOGE(TAG, "cannot build the %s report descriptor", name);
        return;
    }
    itf->desc_len = (uint16_t)n;
    for (unsigned c = 0; c < HID_COLL_COUNT; c++) {
        if (colls & HID_COLL_BIT(c)) {
            s_coll_itf[c] = (int8_t)s_itf_count;
        }
    }
    s_itf_count++;
}

static size_t build_config_descriptor(void)
{
    const uint16_t total = (uint16_t)(TUD_CONFIG_DESC_LEN + s_itf_count * TUD_HID_DESC_LEN);
    const uint8_t head[] = {
        TUD_CONFIG_DESCRIPTOR(1, s_itf_count, 0, total, TUSB_DESC_CONFIG_ATT_REMOTE_WAKEUP, MAX_POWER_MA),
    };
    size_t n = 0;
    memcpy(&s_cfg_desc[n], head, sizeof(head));
    n += sizeof(head);
    for (uint8_t i = 0; i < s_itf_count; i++) {
        const uint8_t itf[] = {
            TUD_HID_DESCRIPTOR(i, 0, s_itf[i].protocol, s_itf[i].desc_len, (uint8_t)(0x81u + i), EP_SIZE, POLL_MS),
        };
        memcpy(&s_cfg_desc[n], itf, sizeof(itf));
        n += sizeof(itf);
    }
    return n;
}

/* ---- TinyUSB callbacks (TinyUSB task: no blocking) ---------------------------------------- */

uint8_t const *tud_hid_descriptor_report_cb(uint8_t instance)
{
    return instance < s_itf_count ? s_itf[instance].desc : NULL;
}

/* Which collection a report of this interface belongs to (report id 0 = the only one). */
static int coll_of(uint8_t instance, uint8_t report_id)
{
    if (instance >= s_itf_count) {
        return -1;
    }
    for (unsigned c = 0; c < HID_COLL_COUNT; c++) {
        if ((s_itf[instance].colls & HID_COLL_BIT(c)) != 0u &&
            (!s_itf[instance].with_ids || hid_coll_report_id((hid_coll_t)c) == report_id)) {
            return (int)c;
        }
    }
    return -1;
}

uint16_t tud_hid_get_report_cb(uint8_t instance, uint8_t report_id, hid_report_type_t report_type, uint8_t *buffer,
                               uint16_t reqlen)
{
    const int c = coll_of(instance, report_id);
    if (c < 0) {
        return 0; /* STALL */
    }
    if (report_type == HID_REPORT_TYPE_OUTPUT && c == HID_COLL_KEYBOARD && reqlen >= 1u) {
        portENTER_CRITICAL(&s_lock);
        buffer[0] = s_leds;
        portEXIT_CRITICAL(&s_lock);
        return 1;
    }
    if (report_type != HID_REPORT_TYPE_INPUT) {
        return 0;
    }
    uint16_t len = hid_coll_input_len((hid_coll_t)c);
    if (len > reqlen) {
        len = reqlen;
    }
    portENTER_CRITICAL(&s_lock);
    memcpy(buffer, s_last[c], len);
    portEXIT_CRITICAL(&s_lock);
    return len;
}

void tud_hid_set_report_cb(uint8_t instance, uint8_t report_id, hid_report_type_t report_type, uint8_t const *buffer,
                           uint16_t bufsize)
{
    (void)report_id;
    if (instance < s_itf_count && s_coll_itf[HID_COLL_KEYBOARD] == (int8_t)instance &&
        report_type == HID_REPORT_TYPE_OUTPUT && bufsize >= 1u) {
        portENTER_CRITICAL(&s_lock);
        s_leds = buffer[0];
        portEXIT_CRITICAL(&s_lock);
    }
}

void tud_hid_report_complete_cb(uint8_t instance, uint8_t const *report, uint16_t len)
{
    (void)report;
    (void)len;
    if (instance < s_itf_count && s_done[instance] != NULL) {
        xSemaphoreGive(s_done[instance]); /* the host has read the report */
    }
}

void tud_mount_cb(void)
{
    portENTER_CRITICAL(&s_lock);
    s_stats.mounts++;
    s_stalled = false;
    portEXIT_CRITICAL(&s_lock);
    ESP_LOGI(TAG, "configured by the host");
}

void tud_umount_cb(void)
{
    portENTER_CRITICAL(&s_lock);
    s_leds = 0;
    portEXIT_CRITICAL(&s_lock);
    ESP_LOGI(TAG, "detached / unconfigured");
}

void tud_suspend_cb(bool remote_wakeup_en)
{
    ESP_LOGI(TAG, "bus suspended (remote wakeup %s)", remote_wakeup_en ? "allowed" : "not allowed");
}

void tud_resume_cb(void)
{
    ESP_LOGI(TAG, "bus resumed");
}

/* ---- hid_link ----------------------------------------------------------------------------- */

esp_err_t hid_link_start(const hid_link_config_t *cfg)
{
    s_colls = cfg->collections & HID_COLL_ALL;
    s_primary = hid_link_primary(s_colls);
    memset(s_coll_itf, -1, sizeof(s_coll_itf));
    add_itf(HID_COLL_BIT(HID_COLL_KEYBOARD), false, HID_ITF_PROTOCOL_KEYBOARD, "keyboard");
    add_itf(HID_COLL_BIT(HID_COLL_MOUSE), false, HID_ITF_PROTOCOL_MOUSE, "mouse");
    add_itf(HID_COLL_BIT(HID_COLL_ABS_POINTER), false, HID_ITF_PROTOCOL_NONE, "pointer");
    add_itf(HID_COLL_BIT(HID_COLL_CONSUMER) | HID_COLL_BIT(HID_COLL_SYSTEM), true, HID_ITF_PROTOCOL_NONE, "extras");
    if (s_itf_count == 0u) {
        return ESP_ERR_INVALID_ARG;
    }
    for (uint8_t i = 0; i < s_itf_count; i++) {
        s_done[i] = xSemaphoreCreateBinary();
        if (s_done[i] == NULL) {
            return ESP_ERR_NO_MEM;
        }
    }

    snprintf(s_manufacturer, sizeof(s_manufacturer), "%s",
             (cfg->manufacturer != NULL && cfg->manufacturer[0] != '\0') ? cfg->manufacturer : "iphone-hid");
    snprintf(s_product, sizeof(s_product), "%s", cfg->name != NULL ? cfg->name : "HID Bridge");
    snprintf(s_serial, sizeof(s_serial), "%s", cfg->serial != NULL ? cfg->serial : "0");
    s_strings[0] = LANG_EN_US;
    s_strings[1] = s_manufacturer;
    s_strings[2] = s_product;
    s_strings[3] = s_serial;

    s_dev_desc = (tusb_desc_device_t){
        .bLength = sizeof(tusb_desc_device_t),
        .bDescriptorType = TUSB_DESC_DEVICE,
        .bcdUSB = 0x0200,
        .bDeviceClass = 0x00, /* class per interface */
        .bDeviceSubClass = 0x00,
        .bDeviceProtocol = 0x00,
        .bMaxPacketSize0 = CFG_TUD_ENDPOINT0_SIZE,
        .idVendor = cfg->vid,
        .idProduct = cfg->pid,
        .bcdDevice = 0x0100,
        .iManufacturer = 1,
        .iProduct = 2,
        .iSerialNumber = 3,
        .bNumConfigurations = 1,
    };
    const size_t cfg_len = build_config_descriptor();

    const tinyusb_config_t tusb_cfg = {
        .device_descriptor = &s_dev_desc,
        .string_descriptor = s_strings,
        .string_descriptor_count = 4,
        .external_phy = false,
        .configuration_descriptor = s_cfg_desc,
        .self_powered = false,
    };
    const esp_err_t err = tinyusb_driver_install(&tusb_cfg);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "tinyusb_driver_install: %s", esp_err_to_name(err));
        return err;
    }
    ESP_LOGI(TAG, "USB %04X:%04X \"%s\", %u interface(s), configuration descriptor %u bytes", cfg->vid, cfg->pid,
             s_product, (unsigned)s_itf_count, (unsigned)cfg_len);
    for (uint8_t i = 0; i < s_itf_count; i++) {
        ESP_LOGI(TAG, "  interface %u: %s, report descriptor %u bytes", (unsigned)i, s_itf[i].name,
                 (unsigned)s_itf[i].desc_len);
    }
    return ESP_OK;
}

uint8_t hid_link_send(hid_coll_t which, const uint8_t *data, size_t len)
{
    if ((unsigned)which >= HID_COLL_COUNT || s_coll_itf[which] < 0 || data == NULL ||
        len != hid_coll_input_len(which) || len > MAX_IN_LEN) {
        portENTER_CRITICAL(&s_lock);
        s_stats.refused++;
        portEXIT_CRITICAL(&s_lock);
        return CH9329_STATUS_EXEC_FAILED;
    }
    const uint8_t inst = (uint8_t)s_coll_itf[which];
    const uint8_t report_id = s_itf[inst].with_ids ? hid_coll_report_id(which) : 0u;
    const int64_t deadline = esp_timer_get_time() + (int64_t)CONFIG_BRIDGE_NOTIFY_WAIT_MS * 1000;
    bool wake_requested = false;

    /* 1. Wait (bounded) until the endpoint is free: the previous report has been read. */
    for (;;) {
        if (!tud_mounted()) {
            portENTER_CRITICAL(&s_lock);
            s_stats.refused++;
            portEXIT_CRITICAL(&s_lock);
            return CH9329_STATUS_EXEC_FAILED;
        }
        if (tud_suspended()) {
            /* The phone suspended the bus (asleep): ask it to resume, if it allowed that. */
            if (!wake_requested) {
                wake_requested = true;
                if (tud_remote_wakeup()) {
                    portENTER_CRITICAL(&s_lock);
                    s_stats.wakeups++;
                    portEXIT_CRITICAL(&s_lock);
                }
            }
        } else if (tud_hid_n_ready(inst)) {
            break;
        }
        if (esp_timer_get_time() >= deadline) {
            portENTER_CRITICAL(&s_lock);
            s_stats.busy++;
            s_stalled = true;
            portEXIT_CRITICAL(&s_lock);
            return CH9329_STATUS_EXEC_FAILED;
        }
        vTaskDelay(1);
    }

    /* 2. Queue it. A completion left over from an earlier, unconfirmed report is dropped first:
     * the endpoint was free, so that report is done and this wait is for the new one. */
    (void)xSemaphoreTake(s_done[inst], 0);
    if (!tud_hid_n_report(inst, report_id, data, (uint16_t)len)) {
        portENTER_CRITICAL(&s_lock);
        s_stats.rejected++;
        portEXIT_CRITICAL(&s_lock);
        return CH9329_STATUS_EXEC_FAILED;
    }

    /* 3. Wait for the host to read it. */
    if (xSemaphoreTake(s_done[inst], pdMS_TO_TICKS(CONFIG_BRIDGE_USB_CONFIRM_WAIT_MS)) != pdTRUE) {
        /* Still in the endpoint: the phone stopped polling (detached, suspended mid-transfer).
         * It may still be read later if polling resumes; the host must treat the pointer
         * position as unknown after this E6. */
        portENTER_CRITICAL(&s_lock);
        s_stats.unconfirmed++;
        s_stalled = true;
        portEXIT_CRITICAL(&s_lock);
        ESP_LOGW(TAG, "%s report not confirmed within %d ms", hid_coll_name(which), CONFIG_BRIDGE_USB_CONFIRM_WAIT_MS);
        return CH9329_STATUS_EXEC_FAILED;
    }
    portENTER_CRITICAL(&s_lock);
    memcpy(s_last[which], data, len);
    s_stalled = false;
    s_stats.sent++;
    portEXIT_CRITICAL(&s_lock);
    return CH9329_STATUS_OK;
}

bool hid_link_ready(void)
{
    if (!tud_mounted() || tud_suspended()) {
        return false;
    }
    /* A stall ends once every endpoint is free again (the stuck report was read). */
    bool all_free = true;
    for (uint8_t i = 0; i < s_itf_count; i++) {
        if ((s_itf[i].colls & s_primary) != 0u && !tud_hid_n_ready(i)) {
            all_free = false;
        }
    }
    portENTER_CRITICAL(&s_lock);
    if (s_stalled && all_free) {
        s_stalled = false;
    }
    const bool ready = !s_stalled;
    portEXIT_CRITICAL(&s_lock);
    return ready;
}

uint8_t hid_link_leds(void)
{
    portENTER_CRITICAL(&s_lock);
    const uint8_t leds = s_leds;
    portEXIT_CRITICAL(&s_lock);
    return leds;
}

void hid_link_shutdown(uint32_t timeout_ms)
{
    (void)tud_disconnect(); /* the phone sees a detach at once, not only when the PHY resets */
    vTaskDelay(pdMS_TO_TICKS(timeout_ms < 20u ? timeout_ms : 20u));
}

void hid_link_forget_peers(void)
{
    ESP_LOGI(TAG, "USB output: no bonds to delete");
}

uint8_t hid_link_output_id(void)
{
    return CH9329_OUTPUT_USB;
}

void hid_link_log_stats(void)
{
    portENTER_CRITICAL(&s_lock);
    const usb_stats_t st = s_stats;
    portEXIT_CRITICAL(&s_lock);
    ESP_LOGI(TAG, "usb: mounted %d suspended %d | sent %lu refused %lu busy %lu rejected %lu unconfirmed %lu wakeups %lu",
             tud_mounted(), tud_suspended(), (unsigned long)st.sent, (unsigned long)st.refused, (unsigned long)st.busy,
             (unsigned long)st.rejected, (unsigned long)st.unconfirmed, (unsigned long)st.wakeups);
}

#endif /* CONFIG_BRIDGE_OUTPUT_USB */
