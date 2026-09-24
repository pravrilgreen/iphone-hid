/*
 * BLE HID over GATT peripheral on NimBLE: the hid_link implementation for
 * CONFIG_BRIDGE_OUTPUT_BLE (see hid_link.h for the delivery contract).
 *
 * GATT database (identical in every work mode, so a bonded iPhone's cached handles stay valid;
 * only the Report Map and Report Reference values change with the profile, announced with
 * Service Changed):
 *
 *   Device Information 0x180A  Manufacturer, Model, Serial, Firmware rev, Software rev, PnP ID
 *   Battery            0x180F  Battery Level (always 100 %: USB powered)
 *   HID                0x1812  HID Information, Report Map, HID Control Point, Protocol Mode,
 *                              Report id 1 input (keyboard), id 1 output (LEDs), id 2 input
 *                              (mouse), id 3 input (consumer), id 4 input (system), id 5 input
 *                              (absolute pointer)
 *
 * Report ids (hid_map_build(), hid_map_report_id()): a profile with several collections uses the
 * ids above. A profile with ONE collection (keyboard-only; mouse-only with a single pointer) has
 * a Report Map without any Report ID item and that collection's Report References say id 0:
 * iOS 13.2.3 ignored notifications from a single-collection map that declared an id (Apple
 * Developer Forums thread 126757). Characteristics of collections outside the profile keep their
 * own id, which the map does not declare; they are never notified.
 *
 * Readable input values start idle (hid_coll_idle_report()): all zero, except the absolute
 * pointer, which reads as the centre of the screen. An absolute report is a position, and (0,0)
 * would send the phone's pointer to the top-left corner.
 *
 * Threading: hid_link_send() and hid_link_shutdown() block and run in the bridge task, never in
 * the NimBLE host task. GAP/GATT callbacks (host task) only copy small values and never wait.
 *
 * API usage follows the ESP-IDF NimBLE examples (examples/bluetooth/nimble/bleprph and blehr,
 * Apache-2.0 / CC0), the NimBLE host headers and ESP-IDF's own nimble HID service
 * (components/bt/host/nimble/nimble/nimble/host/services/hid, Apache-2.0) for the HID
 * characteristic permissions. No example code was copied.
 *
 * Apple "Accessory Design Guidelines for Apple Devices" (edition of 2026-09-21), chapter
 * "Bluetooth Low Energy (BLE)", applied here:
 *   - ADV_IND (never ADV_DIRECT_IND); Flags, TX Power Level, Local Name and the HID service in
 *     the advertising data; the Local Name without ':' or ';'.
 *   - Advertising interval 20 ms for the first 30 s, then 152.5 ms (one of Apple's values).
 *   - No pairing request from the accessory: encrypted-only characteristics make the iPhone
 *     receive "Insufficient Authentication" and start pairing itself.
 *   - Connection parameters: Interval Min >= 15 ms and a multiple of 15 ms, Interval Max >= Min
 *     + 15 ms or both 15 ms (HID: 11.25 ms "may be accepted by some devices"), latency <= 30,
 *     supervision timeout 6 s to 18 s.
 *   - Device Information Service not advertised; Service Changed present because the Report Map
 *     changes with the work mode.
 */
#include "sdkconfig.h"

#if CONFIG_BRIDGE_OUTPUT_BLE

#include <stdio.h>
#include <string.h>

#include "bridge_config.h"
#include "esp_app_desc.h"
#include "esp_log.h"
#include "esp_mac.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/event_groups.h"
#include "freertos/task.h"
#include "host/ble_hs.h"
#include "host/ble_store.h"
#include "hid_link.h"
#include "host/util/util.h"
#include "nimble/nimble_port.h"
#include "nimble/nimble_port_freertos.h"
#include "os/os_mbuf.h"
#include "persist_nvs.h"
#include "services/gap/ble_svc_gap.h"
#include "services/gatt/ble_svc_gatt.h"

void ble_store_config_init(void); /* NimBLE's NVS-backed bond store; declared by no public header */

static const char *TAG = "ble_hid";

/* ---- constants ---------------------------------------------------------------------------- */

#define UUID_SVC_DIS 0x180Au
#define UUID_SVC_BATTERY 0x180Fu
#define UUID_SVC_HID 0x1812u
#define UUID_CHR_MANUFACTURER 0x2A29u
#define UUID_CHR_MODEL 0x2A24u
#define UUID_CHR_SERIAL 0x2A25u
#define UUID_CHR_FW_REV 0x2A26u
#define UUID_CHR_SW_REV 0x2A28u
#define UUID_CHR_PNP_ID 0x2A50u
#define UUID_CHR_BATTERY_LEVEL 0x2A19u
#define UUID_CHR_HID_INFO 0x2A4Au
#define UUID_CHR_REPORT_MAP 0x2A4Bu
#define UUID_CHR_HID_CONTROL 0x2A4Cu
#define UUID_CHR_REPORT 0x2A4Du
#define UUID_CHR_PROTOCOL_MODE 0x2A4Eu
#define UUID_DSC_REPORT_REF 0x2908u

#define APPEARANCE_KEYBOARD 0x03C1u
#define APPEARANCE_MOUSE 0x03C2u

#define ADV_FAST_ITVL 32u   /* 20 ms in 0.625 ms units */
#define ADV_SLOW_ITVL 244u  /* 152.5 ms */
#define ADV_FAST_MS 30000   /* Apple: 20 ms for at least 30 s */
#define ADV_NAME_IN_ADV 15u /* 31 - flags(3) - tx power(3) - appearance(4) - uuid16(4) - header(2) */
#define NAME_MAX_LEN HID_LINK_NAME_MAX /* whole name in the scan response: 31 - header(2) */
_Static_assert(CH9329_STR_MAX + 1u + HID_IDENTITY_TAG_MAX <= NAME_MAX_LEN,
               "a SET_USB_STRING product name plus the identity tag must fit the BLE name");

/* HID reports may only use NimBLE's mbuf pools while more blocks than this are free: the
 * stack's own ATT responses (report map reads, CCCD writes) and L2CAP signalling (connection
 * parameter updates) must never starve behind a report burst, or iOS would drop the link
 * after its 30 s ATT timeout. Pools on the ESP32-S3: 12 + 24 blocks. */
#define BRIDGE_MSYS_RESERVE 12

#define PARAM_RETRY_MS 5000u
#define EVT_DISCONNECTED BIT0
#define ITVL_TO_PERIOD 5u /* conn_itvl counts 1.25 ms; GET_INFO's report period counts 0.25 ms */

/* HID Information: bcdHID 1.11, country 0, flags RemoteWake | NormallyConnectable. */
static const uint8_t HID_INFO[4] = {0x11, 0x01, 0x00, 0x03};

/* Report Reference descriptors: {report id, type (1 input, 2 output)}. The id depends on the
 * profile's Report Map (hid_map_report_id()); the table is filled in hid_link_start(). */
enum { RR_KB_IN = 0, RR_KB_OUT, RR_MOUSE_IN, RR_CONSUMER_IN, RR_SYSTEM_IN, RR_ABS_IN, RR_COUNT };
static const hid_coll_t RR_COLL[RR_COUNT] = {HID_COLL_KEYBOARD, HID_COLL_KEYBOARD, HID_COLL_MOUSE,
                                             HID_COLL_CONSUMER, HID_COLL_SYSTEM,   HID_COLL_ABS_POINTER};
static const uint8_t RR_TYPE[RR_COUNT] = {1, 2, 1, 1, 1, 1};

#define N_IN HID_COLL_COUNT /* one input report characteristic per collection */
#define MAX_IN_LEN 8u       /* largest input payload (keyboard) */

enum {
    DIS_MANUFACTURER = 0,
    DIS_MODEL,
    DIS_SERIAL,
    DIS_FW_REV,
    DIS_SW_REV,
    DIS_PNP,
};

enum {
    HID_CHR_INFO = 0,
    HID_CHR_MAP,
    HID_CHR_CONTROL,
    HID_CHR_PROTOCOL,
    HID_CHR_KB_OUT,
    HID_CHR_IN_BASE, /* + hid_coll_t */
};

/* ---- state -------------------------------------------------------------------------------- */

/* Shared between the NimBLE host task (writer, in GAP/GATT callbacks) and the bridge task
 * (reader, in hid_link_send / hid_link_ready). Short critical sections only. */
static portMUX_TYPE s_lock = portMUX_INITIALIZER_UNLOCKED;
static struct {
    uint16_t conn;
    bool encrypted;
    bool subscribed[N_IN];
    bool stalled; /* buffers stayed full for a whole wait: link presumed dead until progress */
    uint8_t leds;
    uint8_t protocol_mode;
    bool suspended;
    uint16_t period; /* connection interval in 0.25 ms units (GET_INFO bytes 6-7), 0 = none */
    uint8_t last[N_IN][MAX_IN_LEN]; /* value returned on a GATT read */
} s_st = {.conn = BLE_HS_CONN_HANDLE_NONE, .protocol_mode = 1};

typedef struct {
    uint32_t notify_ok;
    uint32_t notify_refused; /* not connected / not encrypted / not subscribed / not in profile */
    uint32_t notify_timeout; /* buffers stayed full for CONFIG_BRIDGE_NOTIFY_WAIT_MS */
    uint32_t notify_error;   /* other NimBLE errors */
    uint32_t buffer_waits;   /* reports that had to wait for a buffer at least once */
    uint32_t connections;
} ble_stats_t;
static ble_stats_t s_stats;

/* Set once in hid_link_start(), read-only afterwards. */
static hid_profile_t s_profile;
static unsigned s_colls;   /* collections in the Report Map */
static unsigned s_primary; /* ... that GET_INFO's link status requires */
static uint16_t s_appearance;
static char s_name[NAME_MAX_LEN + 1];
static char s_manufacturer[CH9329_STR_MAX + 1];
static char s_serial[HID_LINK_SERIAL_MAX + 1];
static char s_fw_rev[48]; /* esp_app_desc_t.version: 32 bytes */
static char s_sw_rev[48]; /* "ESP-IDF " + esp_app_desc_t.idf_ver (32 bytes) */
static uint8_t s_pnp[7];
static const char *const MODEL = "ESP32-S3 BLE HID bridge"; /* <= 26 chars (Apple) */
static uint8_t s_map[HID_DESC_MAX];
static size_t s_map_len;
static uint8_t s_report_ref[RR_COUNT][2];

/* Bridge task only. */
static uint32_t s_accepted_us; /* hid_link_accepted_us() */

/* Host task only. */
static uint8_t s_own_addr_type;
static bool s_db_changed;
static bool s_clear_bonds_pending;
static bool s_param_retried;
static struct ble_npl_event s_clear_ev;
static struct ble_npl_callout s_param_retry;
static EventGroupHandle_t s_events;

static uint16_t s_in_handle[N_IN];
static uint16_t s_kb_out_handle;
static uint16_t s_battery_handle;

static int gap_event(struct ble_gap_event *event, void *arg);

/* ---- GATT access callbacks (NimBLE host task: no blocking) -------------------------------- */

static int append(struct os_mbuf *om, const void *data, size_t len)
{
    return os_mbuf_append(om, data, (uint16_t)len) == 0 ? 0 : BLE_ATT_ERR_INSUFFICIENT_RES;
}

/* Copy a written value of exactly `len` bytes. */
static int read_write(const struct ble_gatt_access_ctxt *ctxt, uint8_t *out, uint16_t len)
{
    if (OS_MBUF_PKTLEN(ctxt->om) != len) {
        return BLE_ATT_ERR_INVALID_ATTR_VALUE_LEN;
    }
    uint16_t got = 0;
    if (ble_hs_mbuf_to_flat(ctxt->om, out, len, &got) != 0 || got != len) {
        return BLE_ATT_ERR_UNLIKELY;
    }
    return 0;
}

static int dis_access(uint16_t conn_handle, uint16_t attr_handle, struct ble_gatt_access_ctxt *ctxt, void *arg)
{
    (void)conn_handle;
    (void)attr_handle;
    if (ctxt->op != BLE_GATT_ACCESS_OP_READ_CHR) {
        return BLE_ATT_ERR_UNLIKELY;
    }
    switch ((uintptr_t)arg) {
    case DIS_MANUFACTURER:
        return append(ctxt->om, s_manufacturer, strlen(s_manufacturer));
    case DIS_MODEL:
        return append(ctxt->om, MODEL, strlen(MODEL));
    case DIS_SERIAL:
        return append(ctxt->om, s_serial, strlen(s_serial));
    case DIS_FW_REV:
        return append(ctxt->om, s_fw_rev, strlen(s_fw_rev));
    case DIS_SW_REV:
        return append(ctxt->om, s_sw_rev, strlen(s_sw_rev));
    case DIS_PNP:
        return append(ctxt->om, s_pnp, sizeof(s_pnp));
    default:
        return BLE_ATT_ERR_UNLIKELY;
    }
}

static int battery_access(uint16_t conn_handle, uint16_t attr_handle, struct ble_gatt_access_ctxt *ctxt, void *arg)
{
    (void)conn_handle;
    (void)attr_handle;
    (void)arg;
    if (ctxt->op != BLE_GATT_ACCESS_OP_READ_CHR) {
        return BLE_ATT_ERR_UNLIKELY;
    }
    const uint8_t level = 100; /* USB powered */
    return append(ctxt->om, &level, 1);
}

static int report_ref_access(uint16_t conn_handle, uint16_t attr_handle, struct ble_gatt_access_ctxt *ctxt,
                             void *arg)
{
    (void)conn_handle;
    (void)attr_handle;
    const uintptr_t idx = (uintptr_t)arg;
    if (ctxt->op != BLE_GATT_ACCESS_OP_READ_DSC || idx >= RR_COUNT) {
        return BLE_ATT_ERR_UNLIKELY;
    }
    return append(ctxt->om, s_report_ref[idx], 2);
}

static int hid_access(uint16_t conn_handle, uint16_t attr_handle, struct ble_gatt_access_ctxt *ctxt, void *arg)
{
    (void)conn_handle;
    (void)attr_handle;
    const uintptr_t which = (uintptr_t)arg;
    const bool read = ctxt->op == BLE_GATT_ACCESS_OP_READ_CHR;
    const bool write = ctxt->op == BLE_GATT_ACCESS_OP_WRITE_CHR;
    uint8_t v = 0;
    int rc;

    switch (which) {
    case HID_CHR_INFO:
        return read ? append(ctxt->om, HID_INFO, sizeof(HID_INFO)) : BLE_ATT_ERR_UNLIKELY;
    case HID_CHR_MAP:
        return read ? append(ctxt->om, s_map, s_map_len) : BLE_ATT_ERR_UNLIKELY;
    case HID_CHR_CONTROL:
        if (!write) {
            return BLE_ATT_ERR_UNLIKELY;
        }
        rc = read_write(ctxt, &v, 1);
        if (rc == 0 && v <= 1u) {
            portENTER_CRITICAL(&s_lock);
            s_st.suspended = v == 0u; /* 0 Suspend, 1 Exit Suspend: a power hint, reports still flow */
            portEXIT_CRITICAL(&s_lock);
        }
        return rc;
    case HID_CHR_PROTOCOL:
        if (read) {
            portENTER_CRITICAL(&s_lock);
            v = s_st.protocol_mode;
            portEXIT_CRITICAL(&s_lock);
            return append(ctxt->om, &v, 1);
        }
        rc = read_write(ctxt, &v, 1);
        if (rc == 0 && v <= 1u) {
            /* Report mode only: a boot-mode request is recorded and logged, reports stay the same. */
            portENTER_CRITICAL(&s_lock);
            s_st.protocol_mode = v;
            portEXIT_CRITICAL(&s_lock);
        }
        return rc;
    case HID_CHR_KB_OUT:
        if (read) {
            portENTER_CRITICAL(&s_lock);
            v = s_st.leds;
            portEXIT_CRITICAL(&s_lock);
            return append(ctxt->om, &v, 1);
        }
        rc = read_write(ctxt, &v, 1);
        if (rc == 0) {
            portENTER_CRITICAL(&s_lock);
            s_st.leds = v;
            portEXIT_CRITICAL(&s_lock);
        }
        return rc;
    default:
        if (which >= HID_CHR_IN_BASE && which < HID_CHR_IN_BASE + N_IN && read) {
            const size_t in = which - HID_CHR_IN_BASE;
            uint8_t copy[MAX_IN_LEN];
            portENTER_CRITICAL(&s_lock);
            memcpy(copy, s_st.last[in], sizeof(copy));
            portEXIT_CRITICAL(&s_lock);
            return append(ctxt->om, copy, hid_coll_input_len((hid_coll_t)in));
        }
        return BLE_ATT_ERR_UNLIKELY;
    }
}

/* ---- GATT database ------------------------------------------------------------------------ */

#define F_ENC_READ (BLE_GATT_CHR_F_READ | BLE_GATT_CHR_F_READ_ENC)

#define INPUT_REPORT(in, rr)                                                                          \
    {                                                                                                 \
        .uuid = BLE_UUID16_DECLARE(UUID_CHR_REPORT), .access_cb = hid_access,                         \
        .arg = (void *)(uintptr_t)(HID_CHR_IN_BASE + (in)), .val_handle = &s_in_handle[(in)],         \
        .flags = F_ENC_READ | BLE_GATT_CHR_F_NOTIFY,                                                  \
        .descriptors = (struct ble_gatt_dsc_def[]){                                                   \
            {.uuid = BLE_UUID16_DECLARE(UUID_DSC_REPORT_REF), .att_flags = BLE_ATT_F_READ,            \
             .access_cb = report_ref_access, .arg = (void *)(uintptr_t)(rr)},                         \
            {0},                                                                                      \
        },                                                                                            \
    }

#define DIS_CHR(uuid16, which)                                                                        \
    {                                                                                                 \
        .uuid = BLE_UUID16_DECLARE(uuid16), .access_cb = dis_access, .arg = (void *)(uintptr_t)(which), \
        .flags = BLE_GATT_CHR_F_READ,                                                                 \
    }

static const struct ble_gatt_svc_def GATT_SERVICES[] = {
    {
        .type = BLE_GATT_SVC_TYPE_PRIMARY,
        .uuid = BLE_UUID16_DECLARE(UUID_SVC_DIS),
        .characteristics =
            (struct ble_gatt_chr_def[]){
                DIS_CHR(UUID_CHR_MANUFACTURER, DIS_MANUFACTURER),
                DIS_CHR(UUID_CHR_MODEL, DIS_MODEL),
                DIS_CHR(UUID_CHR_SERIAL, DIS_SERIAL),
                DIS_CHR(UUID_CHR_FW_REV, DIS_FW_REV),
                DIS_CHR(UUID_CHR_SW_REV, DIS_SW_REV),
                DIS_CHR(UUID_CHR_PNP_ID, DIS_PNP),
                {0},
            },
    },
    {
        .type = BLE_GATT_SVC_TYPE_PRIMARY,
        .uuid = BLE_UUID16_DECLARE(UUID_SVC_BATTERY),
        .characteristics =
            (struct ble_gatt_chr_def[]){
                {
                    .uuid = BLE_UUID16_DECLARE(UUID_CHR_BATTERY_LEVEL),
                    .access_cb = battery_access,
                    .val_handle = &s_battery_handle,
                    .flags = BLE_GATT_CHR_F_READ | BLE_GATT_CHR_F_NOTIFY,
                },
                {0},
            },
    },
    {
        .type = BLE_GATT_SVC_TYPE_PRIMARY,
        .uuid = BLE_UUID16_DECLARE(UUID_SVC_HID),
        .characteristics =
            (struct ble_gatt_chr_def[]){
                {
                    .uuid = BLE_UUID16_DECLARE(UUID_CHR_HID_INFO),
                    .access_cb = hid_access,
                    .arg = (void *)(uintptr_t)HID_CHR_INFO,
                    .flags = F_ENC_READ,
                },
                {
                    .uuid = BLE_UUID16_DECLARE(UUID_CHR_REPORT_MAP),
                    .access_cb = hid_access,
                    .arg = (void *)(uintptr_t)HID_CHR_MAP,
                    .flags = F_ENC_READ,
                },
                {
                    .uuid = BLE_UUID16_DECLARE(UUID_CHR_HID_CONTROL),
                    .access_cb = hid_access,
                    .arg = (void *)(uintptr_t)HID_CHR_CONTROL,
                    .flags = BLE_GATT_CHR_F_WRITE_NO_RSP | BLE_GATT_CHR_F_WRITE_ENC,
                },
                {
                    .uuid = BLE_UUID16_DECLARE(UUID_CHR_PROTOCOL_MODE),
                    .access_cb = hid_access,
                    .arg = (void *)(uintptr_t)HID_CHR_PROTOCOL,
                    .flags = F_ENC_READ | BLE_GATT_CHR_F_WRITE_NO_RSP | BLE_GATT_CHR_F_WRITE_ENC,
                },
                INPUT_REPORT(HID_COLL_KEYBOARD, RR_KB_IN),
                {
                    .uuid = BLE_UUID16_DECLARE(UUID_CHR_REPORT),
                    .access_cb = hid_access,
                    .arg = (void *)(uintptr_t)HID_CHR_KB_OUT,
                    .val_handle = &s_kb_out_handle,
                    .flags = F_ENC_READ | BLE_GATT_CHR_F_WRITE | BLE_GATT_CHR_F_WRITE_NO_RSP |
                             BLE_GATT_CHR_F_WRITE_ENC,
                    .descriptors =
                        (struct ble_gatt_dsc_def[]){
                            {.uuid = BLE_UUID16_DECLARE(UUID_DSC_REPORT_REF),
                             .att_flags = BLE_ATT_F_READ,
                             .access_cb = report_ref_access,
                             .arg = (void *)(uintptr_t)RR_KB_OUT},
                            {0},
                        },
                },
                INPUT_REPORT(HID_COLL_MOUSE, RR_MOUSE_IN),
                INPUT_REPORT(HID_COLL_CONSUMER, RR_CONSUMER_IN),
                INPUT_REPORT(HID_COLL_SYSTEM, RR_SYSTEM_IN),
                INPUT_REPORT(HID_COLL_ABS_POINTER, RR_ABS_IN),
                {0},
            },
    },
    {0},
};

/* ---- link state helpers ------------------------------------------------------------------- */

static bool in_profile(hid_coll_t c)
{
    return (s_colls & HID_COLL_BIT(c)) != 0u;
}

static void reset_link_state(uint16_t conn)
{
    portENTER_CRITICAL(&s_lock);
    s_st.conn = conn;
    s_st.encrypted = false;
    memset(s_st.subscribed, 0, sizeof(s_st.subscribed));
    s_st.stalled = false;
    s_st.leds = 0;
    s_st.protocol_mode = 1;
    s_st.suspended = false;
    s_st.period = 0;
    for (unsigned c = 0; c < N_IN; c++) {
        (void)hid_coll_idle_report((hid_coll_t)c, s_st.last[c], sizeof(s_st.last[c])); /* never (0,0) */
    }
    portEXIT_CRITICAL(&s_lock);
}

/* ---- advertising / connection parameters (host task) -------------------------------------- */

static void advertise(bool fast)
{
    if (ble_gap_adv_active()) {
        return;
    }
    struct ble_hs_adv_fields adv;
    memset(&adv, 0, sizeof(adv));
    adv.flags = BLE_HS_ADV_F_DISC_GEN | BLE_HS_ADV_F_BREDR_UNSUP;
    adv.tx_pwr_lvl_is_present = 1;
    adv.tx_pwr_lvl = BLE_HS_ADV_TX_PWR_LVL_AUTO;
    adv.appearance = s_appearance;
    adv.appearance_is_present = 1;
    static const ble_uuid16_t hid_uuid = BLE_UUID16_INIT(UUID_SVC_HID);
    adv.uuids16 = &hid_uuid;
    adv.num_uuids16 = 1;
    adv.uuids16_is_complete = 1;
    const size_t name_len = strlen(s_name);
    adv.name = (const uint8_t *)s_name;
    adv.name_len = (uint8_t)(name_len <= ADV_NAME_IN_ADV ? name_len : ADV_NAME_IN_ADV);
    adv.name_is_complete = name_len <= ADV_NAME_IN_ADV;
    int rc = ble_gap_adv_set_fields(&adv);
    if (rc != 0) {
        ESP_LOGE(TAG, "advertising data rejected: %d", rc);
        return;
    }
    struct ble_hs_adv_fields rsp;
    memset(&rsp, 0, sizeof(rsp));
    rsp.name = (const uint8_t *)s_name;
    rsp.name_len = (uint8_t)name_len;
    rsp.name_is_complete = 1;
    rc = ble_gap_adv_rsp_set_fields(&rsp);
    if (rc != 0) {
        ESP_LOGE(TAG, "scan response rejected: %d", rc);
        return;
    }
    struct ble_gap_adv_params params;
    memset(&params, 0, sizeof(params));
    params.conn_mode = BLE_GAP_CONN_MODE_UND; /* ADV_IND */
    params.disc_mode = BLE_GAP_DISC_MODE_GEN;
    params.itvl_min = fast ? ADV_FAST_ITVL : ADV_SLOW_ITVL;
    params.itvl_max = params.itvl_min;
    rc = ble_gap_adv_start(s_own_addr_type, NULL, fast ? ADV_FAST_MS : BLE_HS_FOREVER, &params, gap_event, NULL);
    if (rc != 0) {
        ESP_LOGE(TAG, "advertising failed to start: %d", rc);
        return;
    }
    ESP_LOGI(TAG, "advertising as \"%s\" (%s)", s_name, fast ? "20 ms for 30 s" : "152.5 ms");
}

static bool params_ok(const struct ble_gap_conn_desc *d)
{
    return d->conn_itvl >= CONFIG_BRIDGE_CONN_ITVL_MIN && d->conn_itvl <= CONFIG_BRIDGE_CONN_ITVL_MAX &&
           d->conn_latency == CONFIG_BRIDGE_CONN_LATENCY &&
           d->supervision_timeout == CONFIG_BRIDGE_CONN_SUPERVISION_TIMEOUT_MS / 10;
}

static void request_conn_params(uint16_t conn)
{
    struct ble_gap_conn_desc desc;
    if (ble_gap_conn_find(conn, &desc) != 0 || params_ok(&desc)) {
        return;
    }
    const struct ble_gap_upd_params p = {
        .itvl_min = CONFIG_BRIDGE_CONN_ITVL_MIN,
        .itvl_max = CONFIG_BRIDGE_CONN_ITVL_MAX,
        .latency = CONFIG_BRIDGE_CONN_LATENCY,
        .supervision_timeout = CONFIG_BRIDGE_CONN_SUPERVISION_TIMEOUT_MS / 10,
        .min_ce_len = 0,
        .max_ce_len = 0,
    };
    const int rc = ble_gap_update_params(conn, &p);
    ESP_LOGI(TAG, "requested interval %u-%u x1.25 ms, latency %u, timeout %u ms (rc %d); current %u x1.25 ms",
             (unsigned)p.itvl_min, (unsigned)p.itvl_max, (unsigned)p.latency,
             (unsigned)CONFIG_BRIDGE_CONN_SUPERVISION_TIMEOUT_MS, rc, (unsigned)desc.conn_itvl);
}

static void param_retry_cb(struct ble_npl_event *ev)
{
    (void)ev;
    portENTER_CRITICAL(&s_lock);
    const uint16_t conn = s_st.conn;
    portEXIT_CRITICAL(&s_lock);
    if (conn != BLE_HS_CONN_HANDLE_NONE) {
        request_conn_params(conn);
    }
}

static void clear_bonds_now(void)
{
    const int rc = ble_store_clear();
    s_clear_bonds_pending = false;
    ESP_LOGW(TAG, "all bonds deleted (rc %d): forget the bridge on the iPhone, then pair again", rc);
}

static void clear_bonds_cb(struct ble_npl_event *ev)
{
    (void)ev;
    portENTER_CRITICAL(&s_lock);
    const uint16_t conn = s_st.conn;
    portEXIT_CRITICAL(&s_lock);
    if (conn != BLE_HS_CONN_HANDLE_NONE) {
        s_clear_bonds_pending = true; /* finished in the DISCONNECT event */
        ble_gap_terminate(conn, BLE_ERR_REM_USER_CONN_TERM);
        return;
    }
    clear_bonds_now();
}

/* ---- GAP events (host task) --------------------------------------------------------------- */

static void log_conn(const char *what, uint16_t conn)
{
    struct ble_gap_conn_desc d;
    if (ble_gap_conn_find(conn, &d) != 0) {
        ESP_LOGI(TAG, "%s (handle %u)", what, (unsigned)conn);
        return;
    }
    ESP_LOGI(TAG, "%s: peer %02X:%02X:%02X:%02X:%02X:%02X, interval %u x1.25 ms, latency %u, timeout %u0 ms, "
                  "encrypted %d, bonded %d",
             what, d.peer_id_addr.val[5], d.peer_id_addr.val[4], d.peer_id_addr.val[3], d.peer_id_addr.val[2],
             d.peer_id_addr.val[1], d.peer_id_addr.val[0], (unsigned)d.conn_itvl, (unsigned)d.conn_latency,
             (unsigned)d.supervision_timeout, d.sec_state.encrypted, d.sec_state.bonded);
}

/* GET_INFO's report period: notifications reach the phone only at connection events, so the
 * connection interval is the grid the host should pace pointer reports on. The descriptor holds
 * the interval in effect (after a failed update too: the old one). */
static void update_period(uint16_t conn)
{
    struct ble_gap_conn_desc d;
    /* conn_itvl <= 3200 (4 s) by the specification: x5 fits in 16 bits. */
    const uint16_t period = ble_gap_conn_find(conn, &d) == 0 ? (uint16_t)(d.conn_itvl * ITVL_TO_PERIOD) : 0u;
    portENTER_CRITICAL(&s_lock);
    if (s_st.conn == conn) {
        s_st.period = period;
    }
    portEXIT_CRITICAL(&s_lock);
}

static int gap_event(struct ble_gap_event *event, void *arg)
{
    (void)arg;
    switch (event->type) {
    case BLE_GAP_EVENT_CONNECT:
        if (event->connect.status != 0) {
            ESP_LOGW(TAG, "connection failed: %d", event->connect.status);
            advertise(true);
            return 0;
        }
        reset_link_state(event->connect.conn_handle);
        update_period(event->connect.conn_handle);
        s_param_retried = false;
        portENTER_CRITICAL(&s_lock);
        s_stats.connections++;
        portEXIT_CRITICAL(&s_lock);
        log_conn("connected", event->connect.conn_handle);
        /* No security request here (Apple 58.10): the first encrypted-only read makes the iPhone
         * pair (new peer) or encrypt with the stored keys (bonded peer). */
        return 0;

    case BLE_GAP_EVENT_DISCONNECT:
        ESP_LOGI(TAG, "disconnected: reason 0x%03X", (unsigned)event->disconnect.reason);
        ble_npl_callout_stop(&s_param_retry);
        reset_link_state(BLE_HS_CONN_HANDLE_NONE);
        xEventGroupSetBits(s_events, EVT_DISCONNECTED);
        if (s_clear_bonds_pending) {
            clear_bonds_now();
        }
        advertise(true);
        return 0;

    case BLE_GAP_EVENT_ENC_CHANGE:
        if (event->enc_change.status != 0) {
            /* Typically: the iPhone still has keys the bridge lost (bonds cleared). It must
             * "Forget This Device" and pair again. */
            ESP_LOGW(TAG, "encryption failed: %d", event->enc_change.status);
            return 0;
        }
        portENTER_CRITICAL(&s_lock);
        s_st.encrypted = true;
        portEXIT_CRITICAL(&s_lock);
        log_conn("encrypted", event->enc_change.conn_handle);
        request_conn_params(event->enc_change.conn_handle);
        return 0;

    case BLE_GAP_EVENT_SUBSCRIBE: {
        portENTER_CRITICAL(&s_lock);
        const bool ours = event->subscribe.conn_handle == s_st.conn;
        int which = -1;
        if (ours) {
            for (int i = 0; i < N_IN; i++) {
                if (event->subscribe.attr_handle == s_in_handle[i]) {
                    s_st.subscribed[i] = event->subscribe.cur_notify != 0;
                    which = i;
                }
            }
        }
        portEXIT_CRITICAL(&s_lock);
        if (which >= 0) {
            ESP_LOGI(TAG, "%s report notifications %s (reason %u)", hid_coll_name((hid_coll_t)which),
                     event->subscribe.cur_notify ? "on" : "off", (unsigned)event->subscribe.reason);
        }
        return 0;
    }

    case BLE_GAP_EVENT_CONN_UPDATE:
        if (event->conn_update.status != 0) {
            ESP_LOGW(TAG, "connection parameter update failed: %d", event->conn_update.status);
        }
        update_period(event->conn_update.conn_handle);
        log_conn("connection parameters", event->conn_update.conn_handle);
        if (!s_param_retried) {
            struct ble_gap_conn_desc d;
            if (ble_gap_conn_find(event->conn_update.conn_handle, &d) == 0 && !params_ok(&d)) {
                s_param_retried = true; /* one more request later, then accept what iOS chose */
                ble_npl_callout_reset(&s_param_retry, ble_npl_time_ms_to_ticks32(PARAM_RETRY_MS));
            }
        }
        return 0;

    case BLE_GAP_EVENT_CONN_UPDATE_REQ:
        return 0; /* accept the central's parameters */

    case BLE_GAP_EVENT_ADV_COMPLETE: {
        portENTER_CRITICAL(&s_lock);
        const bool idle = s_st.conn == BLE_HS_CONN_HANDLE_NONE;
        portEXIT_CRITICAL(&s_lock);
        if (idle) {
            advertise(false); /* the 30 s fast phase ended without a connection */
        }
        return 0;
    }

    case BLE_GAP_EVENT_REPEAT_PAIRING: {
        /* The iPhone "forgot" the bridge and pairs again: drop the old keys and let it. */
        struct ble_gap_conn_desc d;
        if (ble_gap_conn_find(event->repeat_pairing.conn_handle, &d) == 0) {
            ble_store_util_delete_peer(&d.peer_id_addr);
        }
        ESP_LOGI(TAG, "repeat pairing: old bond deleted");
        return BLE_GAP_REPEAT_PAIRING_RETRY;
    }

    case BLE_GAP_EVENT_MTU:
        ESP_LOGI(TAG, "ATT MTU %u", (unsigned)event->mtu.value);
        return 0;

    default:
        return 0;
    }
}

/* ---- host lifecycle ----------------------------------------------------------------------- */

static void on_reset(int reason)
{
    ESP_LOGE(TAG, "NimBLE host reset: reason %d", reason);
    reset_link_state(BLE_HS_CONN_HANDLE_NONE);
}

static void on_sync(void)
{
    int rc = ble_hs_util_ensure_addr(0);
    if (rc == 0) {
        rc = ble_hs_id_infer_auto(0, &s_own_addr_type);
    }
    if (rc != 0) {
        ESP_LOGE(TAG, "no usable Bluetooth address: %d", rc);
        return;
    }
    if (s_db_changed) {
        /* The Report Map or a Report Reference differs from the last boot's (work mode, pointer
         * build, firmware update): tell bonded iPhones to re-read the database. NimBLE indicates
         * at once to connected peers and stores a pending indication for bonded ones, sent when
         * they reconnect. Whether iOS then drops its cached report map is unproven. */
        ble_svc_gatt_changed(0x0001, 0xFFFF);
        s_db_changed = false;
        ESP_LOGW(TAG, "report map new or changed since the last boot: Service Changed queued. An iPhone paired "
                      "before must Forget this device; hold BOOT 3 s to delete the bridge's bonds, then pair again");
    }
    advertise(true);
}

static void host_task(void *param)
{
    (void)param;
    nimble_port_run(); /* returns only after nimble_port_stop() */
    nimble_port_freertos_deinit();
}

static void copy_str(char *dst, size_t cap, const char *src, const char *fallback)
{
    const char *s = (src != NULL && src[0] != '\0') ? src : fallback;
    snprintf(dst, cap, "%s", s);
}

/* What a bonded iPhone caches from this firmware: the Report Map and the Report References
 * (FNV-1a). A new value since the last boot is announced with Service Changed (on_sync). */
static uint32_t map_fingerprint(void)
{
    uint32_t h = 2166136261u;
    for (size_t i = 0; i < s_map_len; i++) {
        h = (h ^ s_map[i]) * 16777619u;
    }
    for (size_t i = 0; i < RR_COUNT; i++) {
        h = (h ^ s_report_ref[i][0]) * 16777619u;
        h = (h ^ s_report_ref[i][1]) * 16777619u;
    }
    return h;
}

esp_err_t hid_link_start(const hid_link_config_t *cfg)
{
    s_events = xEventGroupCreate();
    if (s_events == NULL) {
        return ESP_ERR_NO_MEM;
    }
    s_profile = cfg->profile < HID_PROFILE_COUNT ? cfg->profile : HID_PROFILE_COMPOSITE;
    s_colls = cfg->collections & HID_COLL_ALL;
    s_primary = hid_link_primary(s_colls);
    s_map_len = hid_map_build(s_map, sizeof(s_map), s_colls);
    if (s_map_len == 0u) {
        ESP_LOGE(TAG, "no report map for collections 0x%02X", s_colls);
        return ESP_ERR_INVALID_ARG;
    }
    for (unsigned i = 0; i < RR_COUNT; i++) {
        s_report_ref[i][0] = hid_map_report_id(s_colls, RR_COLL[i]);
        s_report_ref[i][1] = RR_TYPE[i];
    }
    reset_link_state(BLE_HS_CONN_HANDLE_NONE); /* idle input values before the first connection */
    s_appearance = s_profile == HID_PROFILE_MOUSE ? APPEARANCE_MOUSE : APPEARANCE_KEYBOARD;

    /* Local Name: printable ASCII, no ':' or ';' (Apple), at most 29 characters. */
    copy_str(s_name, sizeof(s_name), cfg->name, "HID Bridge");
    for (char *c = s_name; *c != '\0'; c++) {
        if (*c == ':' || *c == ';' || *c < 0x20 || *c > 0x7E) {
            *c = '-';
        }
    }
    copy_str(s_manufacturer, sizeof(s_manufacturer), cfg->manufacturer, "iphone-hid");
    copy_str(s_serial, sizeof(s_serial), cfg->serial, "0");
    const esp_app_desc_t *app = esp_app_get_description();
    snprintf(s_fw_rev, sizeof(s_fw_rev), "%s", app->version);
    snprintf(s_sw_rev, sizeof(s_sw_rev), "ESP-IDF %s", app->idf_ver);
    s_pnp[0] = 0x02; /* vendor id source: USB Implementer's Forum */
    s_pnp[1] = (uint8_t)(cfg->vid & 0xFFu);
    s_pnp[2] = (uint8_t)(cfg->vid >> 8);
    s_pnp[3] = (uint8_t)(cfg->pid & 0xFFu);
    s_pnp[4] = (uint8_t)(cfg->pid >> 8);
    s_pnp[5] = 0x00; /* product version 1.00 */
    s_pnp[6] = 0x01;

    /* The GATT layout never changes; the Report Map and Report References do, with the work
     * mode, the pointer build and firmware updates. Remember what the bonded iPhones last saw so
     * a change can be announced (on_sync). No record (first boot, or a firmware that kept only
     * the collection mask) counts as a change: Service Changed without bonded peers is harmless. */
    const uint32_t fingerprint = map_fingerprint();
    uint32_t last = 0;
    if (!persist_get_u32("gatt_map", &last) || last != fingerprint) {
        s_db_changed = true;
        persist_set_u32("gatt_map", fingerprint);
    }

    esp_err_t err = nimble_port_init();
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "nimble_port_init: %s", esp_err_to_name(err));
        return err;
    }

    ble_hs_cfg.reset_cb = on_reset;
    ble_hs_cfg.sync_cb = on_sync;
    ble_hs_cfg.store_status_cb = ble_store_util_status_rr;
    /* Just Works + LE Secure Connections, bonded; identity keys both ways so the iPhone's
     * private address resolves on reconnection. */
    ble_hs_cfg.sm_io_cap = BLE_SM_IO_CAP_NO_IO;
    ble_hs_cfg.sm_bonding = 1;
    ble_hs_cfg.sm_mitm = 0;
    ble_hs_cfg.sm_sc = 1;
    ble_hs_cfg.sm_our_key_dist = BLE_SM_PAIR_KEY_DIST_ENC | BLE_SM_PAIR_KEY_DIST_ID;
    ble_hs_cfg.sm_their_key_dist = BLE_SM_PAIR_KEY_DIST_ENC | BLE_SM_PAIR_KEY_DIST_ID;

    ble_svc_gap_init();
    ble_svc_gatt_init();
    int rc = ble_gatts_count_cfg(GATT_SERVICES);
    if (rc == 0) {
        rc = ble_gatts_add_svcs(GATT_SERVICES);
    }
    if (rc == 0) {
        rc = ble_svc_gap_device_name_set(s_name);
    }
    if (rc == 0) {
        rc = ble_svc_gap_device_appearance_set(s_appearance);
    }
    if (rc != 0) {
        ESP_LOGE(TAG, "GATT setup failed: %d", rc);
        return ESP_FAIL;
    }
    ble_store_config_init();

    ble_npl_event_init(&s_clear_ev, clear_bonds_cb, NULL);
    ble_npl_callout_init(&s_param_retry, nimble_port_get_dflt_eventq(), param_retry_cb, NULL);

    ESP_LOGI(TAG, "profile %s, collections 0x%02X, name \"%s\", serial \"%s\", report map %u bytes %s report ids",
             hid_profile_name(s_profile), s_colls, s_name, s_serial, (unsigned)s_map_len,
             hid_map_uses_ids(s_colls) ? "with" : "without");
    nimble_port_freertos_init(host_task);
    return ESP_OK;
}

/* ---- bridge-task API ---------------------------------------------------------------------- */

uint8_t hid_link_send(hid_coll_t which, const uint8_t *data, size_t len)
{
    if ((unsigned)which >= N_IN || data == NULL || len != hid_coll_input_len(which) || len > MAX_IN_LEN ||
        !in_profile(which)) {
        /* A report the current Report Map does not declare would be ignored by the iPhone. */
        portENTER_CRITICAL(&s_lock);
        s_stats.notify_refused++;
        portEXIT_CRITICAL(&s_lock);
        return CH9329_STATUS_EXEC_FAILED;
    }
    const int64_t deadline = esp_timer_get_time() + (int64_t)CONFIG_BRIDGE_NOTIFY_WAIT_MS * 1000;
    bool waited = false;
    for (;;) {
        portENTER_CRITICAL(&s_lock);
        const uint16_t conn = s_st.conn;
        const bool deliverable = conn != BLE_HS_CONN_HANDLE_NONE && s_st.encrypted && s_st.subscribed[which];
        portEXIT_CRITICAL(&s_lock);
        if (!deliverable) {
            portENTER_CRITICAL(&s_lock);
            s_stats.notify_refused++;
            portEXIT_CRITICAL(&s_lock);
            return CH9329_STATUS_EXEC_FAILED;
        }

        int rc = BLE_HS_ENOMEM;
        if (os_msys_num_free() > BRIDGE_MSYS_RESERVE) {
            struct os_mbuf *om = ble_hs_mbuf_from_flat(data, (uint16_t)len);
            if (om != NULL) {
                /* Consumes om whatever the outcome. 0 = queued in order on this connection: the
                 * link layer retransmits it until the phone acknowledges or the link drops. */
                rc = ble_gatts_notify_custom(conn, s_in_handle[which], om);
            }
        }
        if (rc == 0) {
            s_accepted_us = (uint32_t)esp_timer_get_time(); /* queued for the next connection event */
            portENTER_CRITICAL(&s_lock);
            memcpy(s_st.last[which], data, len);
            s_st.stalled = false;
            s_stats.notify_ok++;
            if (waited) {
                s_stats.buffer_waits++;
            }
            portEXIT_CRITICAL(&s_lock);
            return CH9329_STATUS_OK;
        }
        if (rc != BLE_HS_ENOMEM) {
            /* ENOTCONN (link dropped since the check) or anything unexpected: not queued. */
            portENTER_CRITICAL(&s_lock);
            s_stats.notify_error++;
            portEXIT_CRITICAL(&s_lock);
            ESP_LOGW(TAG, "%s report not queued: NimBLE error %d", hid_coll_name(which), rc);
            return CH9329_STATUS_EXEC_FAILED;
        }
        waited = true;
        if (esp_timer_get_time() >= deadline) {
            /* Nothing drained for the whole wait: the phone stopped acknowledging (out of range,
             * frozen) long before the supervision timeout will say so. GET_INFO reports the link
             * as down until a report goes through again. */
            portENTER_CRITICAL(&s_lock);
            s_st.stalled = true;
            s_stats.notify_timeout++;
            portEXIT_CRITICAL(&s_lock);
            ESP_LOGW(TAG, "%s report not queued: BLE buffers full for %d ms", hid_coll_name(which),
                     CONFIG_BRIDGE_NOTIFY_WAIT_MS);
            return CH9329_STATUS_EXEC_FAILED;
        }
        vTaskDelay(1); /* 1 ms: give the controller a connection event to drain */
    }
}

bool hid_link_ready(void)
{
    /* A stall ends as soon as the stack's queue drains again (the phone acknowledged), even if
     * no report was sent since: a host that waits for "connected" before sending must see it. */
    const bool drained = os_msys_num_free() > BRIDGE_MSYS_RESERVE;
    portENTER_CRITICAL(&s_lock);
    if (s_st.stalled && drained) {
        s_st.stalled = false;
    }
    bool ready = s_st.conn != BLE_HS_CONN_HANDLE_NONE && s_st.encrypted && !s_st.stalled;
    for (int i = 0; i < N_IN; i++) {
        if ((s_primary & HID_COLL_BIT(i)) != 0u && !s_st.subscribed[i]) {
            ready = false;
        }
    }
    portEXIT_CRITICAL(&s_lock);
    return ready;
}

uint32_t hid_link_accepted_us(void)
{
    return s_accepted_us;
}

uint8_t hid_link_leds(void)
{
    portENTER_CRITICAL(&s_lock);
    const uint8_t leds = s_st.leds;
    portEXIT_CRITICAL(&s_lock);
    return leds;
}

uint16_t hid_link_report_period(void)
{
    /* Set on CONNECT and CONN_UPDATE, cleared on DISCONNECT (host task). */
    portENTER_CRITICAL(&s_lock);
    const uint16_t period = s_st.period;
    portEXIT_CRITICAL(&s_lock);
    return period;
}

void hid_link_shutdown(uint32_t timeout_ms)
{
    portENTER_CRITICAL(&s_lock);
    const uint16_t conn = s_st.conn;
    portEXIT_CRITICAL(&s_lock);
    if (conn == BLE_HS_CONN_HANDLE_NONE) {
        return;
    }
    xEventGroupClearBits(s_events, EVT_DISCONNECTED);
    if (ble_gap_terminate(conn, BLE_ERR_REM_USER_CONN_TERM) == 0) {
        xEventGroupWaitBits(s_events, EVT_DISCONNECTED, pdTRUE, pdTRUE, pdMS_TO_TICKS(timeout_ms));
    }
}

void hid_link_forget_peers(void)
{
    ble_npl_eventq_put(nimble_port_get_dflt_eventq(), &s_clear_ev);
}

uint8_t hid_link_output_id(void)
{
    return CH9329_OUTPUT_BLE;
}

void hid_link_log_stats(void)
{
    portENTER_CRITICAL(&s_lock);
    const ble_stats_t st = s_stats;
    const bool conn = s_st.conn != BLE_HS_CONN_HANDLE_NONE;
    portEXIT_CRITICAL(&s_lock);
    ESP_LOGI(TAG, "ble: connected %d, connections %lu | notify ok %lu refused %lu timeout %lu error %lu waits %lu", conn,
             (unsigned long)st.connections, (unsigned long)st.notify_ok, (unsigned long)st.notify_refused,
             (unsigned long)st.notify_timeout, (unsigned long)st.notify_error, (unsigned long)st.buffer_waits);
}

#endif /* CONFIG_BRIDGE_OUTPUT_BLE */
