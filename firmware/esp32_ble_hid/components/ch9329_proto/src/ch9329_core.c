/*
 * CH9329 command dispatcher and configuration handling.
 *
 * Behaviour that the WCH document leaves open, and the choices made here (mirrored in README.md):
 *   - Reply address: the bridge's own configured address (as the host's simulated chip does).
 *   - Address filter: chip address 0x00 accepts any frame address; 0x01-0xFE accepts only its own
 *     address and 0xFF. 0xFF frames are executed but never answered (also no E1/E4).
 *   - Bytes before a header are skipped silently: E2 is never sent (no address/command to answer).
 *   - LEN > 64: the header is treated as false and skipped silently.
 *   - Checksum mismatch: E4 (if addressed to us), then resync inside the rejected frame.
 *   - Partial frame older than the packet interval: E1 if at least ADDR and CMD arrived.
 *   - Commands that take no data (GET_INFO, GET_PARA_CFG, SET_DEFAULT_CFG, RESET) reject LEN != 0
 *     with E5.
 *   - HID commands check shape (E5), then work mode (E6), then hand the report to the sink, whose
 *     answer is final: OK only if the report was delivered to the phone's HID link, E6 otherwise.
 *     Nothing here answers OK on the sink's behalf. The one documented exception: a platform
 *     built without an absolute pointer accepts and drops SEND_MS_ABS_DATA (or answers E5).
 *   - SEND_MS_ABS_DATA: X/Y above 4095 are E5; the report carries X/Y scaled to 0..32767.
 *   - GET_INFO answers version 0x40 and fills the reserved bytes (see ch9329_proto.h).
 *   - SEND_MS_REL_RUN (0x30, vendor): see ch9329_proto.h. It is the only command that can answer
 *     the vendor status E7 (every report delivered, at least one late).
 *   - SET_PARA_CFG / SET_USB_STRING / SET_DEFAULT_CFG change flash only; GET_* read flash back,
 *     the running configuration changes at the next boot (RESET reboots the bridge).
 */
#include <string.h>

#include "ch9329_proto.h"

/* ---- small helpers ------------------------------------------------------------------------ */

uint8_t ch9329_checksum(const uint8_t *data, size_t len)
{
    uint32_t sum = 0;
    for (size_t i = 0; i < len; i++) {
        sum += data[i];
    }
    return (uint8_t)(sum & 0xFFu);
}

size_t ch9329_encode(uint8_t *out, size_t cap, uint8_t addr, uint8_t cmd, const uint8_t *data, size_t len)
{
    if (out == NULL || len > CH9329_MAX_DATA || cap < len + CH9329_OVERHEAD || (len > 0u && data == NULL)) {
        return 0;
    }
    out[0] = CH9329_HEAD0;
    out[1] = CH9329_HEAD1;
    out[2] = addr;
    out[3] = cmd;
    out[4] = (uint8_t)len;
    if (len > 0u) {
        memcpy(&out[5], data, len);
    }
    out[5u + len] = ch9329_checksum(out, 5u + len);
    return len + CH9329_OVERHEAD;
}

uint16_t ch9329_cfg_u16(const uint8_t cfg[CH9329_CFG_SIZE], size_t off)
{
    return (uint16_t)(((uint16_t)cfg[off] << 8) | cfg[off + 1u]);
}

void ch9329_cfg_put_u16(uint8_t cfg[CH9329_CFG_SIZE], size_t off, uint16_t v)
{
    cfg[off] = (uint8_t)(v >> 8);
    cfg[off + 1u] = (uint8_t)(v & 0xFFu);
}

void ch9329_cfg_put_u32(uint8_t cfg[CH9329_CFG_SIZE], size_t off, uint32_t v)
{
    cfg[off] = (uint8_t)(v >> 24);
    cfg[off + 1u] = (uint8_t)((v >> 16) & 0xFFu);
    cfg[off + 2u] = (uint8_t)((v >> 8) & 0xFFu);
    cfg[off + 3u] = (uint8_t)(v & 0xFFu);
}

uint8_t ch9329_cfg_work_mode(const uint8_t cfg[CH9329_CFG_SIZE])
{
    return cfg[CH9329_CFG_OFF_WORK_MODE];
}

uint8_t ch9329_cfg_address(const uint8_t cfg[CH9329_CFG_SIZE])
{
    return cfg[CH9329_CFG_OFF_ADDRESS];
}

uint32_t ch9329_cfg_baud(const uint8_t cfg[CH9329_CFG_SIZE])
{
    const uint8_t *b = &cfg[CH9329_CFG_OFF_BAUD];
    return ((uint32_t)b[0] << 24) | ((uint32_t)b[1] << 16) | ((uint32_t)b[2] << 8) | (uint32_t)b[3];
}

bool ch9329_baud_supported(uint32_t baud)
{
    switch (baud) {
    case 9600u:
    case 19200u:
    case 38400u:
    case 57600u:
    case 115200u:
        return true;
    default:
        return false;
    }
}

uint32_t ch9329_char_time_ms(uint32_t baud, uint32_t chars)
{
    if (baud == 0u) {
        return 0;
    }
    /* 10 bits per 8N1 character; 64-bit intermediate so large `chars` cannot overflow. */
    const uint64_t bits_ms = (uint64_t)chars * 10u * 1000u;
    return (uint32_t)((bits_ms + baud - 1u) / baud);
}

void ch9329_persist_defaults(ch9329_persist_t *p)
{
    memset(p, 0, sizeof(*p));
    uint8_t *c = p->cfg;
    c[CH9329_CFG_OFF_WORK_MODE] = CH9329_WORK_MODE_COMPOSITE;
    c[CH9329_CFG_OFF_SERIAL_MODE] = 0x00; /* protocol mode: the only one the bridge implements */
    c[CH9329_CFG_OFF_ADDRESS] = 0x00;
    ch9329_cfg_put_u32(c, CH9329_CFG_OFF_BAUD, CH9329_DEFAULT_BAUD);
    ch9329_cfg_put_u16(c, CH9329_CFG_OFF_PACKET_INTERVAL, CH9329_DEFAULT_PACKET_INTERVAL_MS);
    ch9329_cfg_put_u16(c, CH9329_CFG_OFF_VID, CH9329_DEFAULT_VID);
    ch9329_cfg_put_u16(c, CH9329_CFG_OFF_PID, CH9329_DEFAULT_PID);
    ch9329_cfg_put_u16(c, CH9329_CFG_OFF_KB_RELEASE_DELAY, 1u);
    c[CH9329_CFG_OFF_ENTER_CHARS] = 0x0D; /* ASCII-mode fields: stored and returned, never used */
    /* Strings: all empty = "use the firmware's built-in names" (memset above). */
}

bool ch9329_cfg_valid(const uint8_t cfg[CH9329_CFG_SIZE])
{
    if (cfg[CH9329_CFG_OFF_WORK_MODE] > CH9329_WORK_MODE_CUSTOM) {
        return false;
    }
    if (cfg[CH9329_CFG_OFF_SERIAL_MODE] != 0x00u) {
        return false; /* ASCII / transparent modes would cut the host off: not implemented */
    }
    if (cfg[CH9329_CFG_OFF_ADDRESS] == CH9329_ADDR_BROADCAST) {
        return false; /* the bridge would never answer anything again */
    }
    return ch9329_baud_supported(ch9329_cfg_baud(cfg));
}

static bool string_bytes_valid(const uint8_t *s, size_t len)
{
    if (len > CH9329_STR_MAX) {
        return false;
    }
    for (size_t i = 0; i < len; i++) {
        if (s[i] < 0x20u || s[i] > 0x7Eu) {
            return false;
        }
    }
    return true;
}

bool ch9329_strings_valid(const ch9329_persist_t *p)
{
    for (size_t k = 0; k < CH9329_STR_COUNT; k++) {
        const ch9329_string_t *s = &p->str[k];
        if (s->len > CH9329_STR_MAX || s->text[s->len] != '\0' ||
            !string_bytes_valid((const uint8_t *)s->text, s->len)) {
            return false;
        }
    }
    return true;
}

/* ---- replies ----------------------------------------------------------------------------- */

static void send_frame(ch9329_core_t *c, uint8_t cmd, const uint8_t *data, size_t len)
{
    uint8_t frame[CH9329_MAX_FRAME];
    const size_t n = ch9329_encode(frame, sizeof(frame), ch9329_cfg_address(c->active.cfg), cmd, data, len);
    if (n == 0u || c->sink.send_reply == NULL) {
        return;
    }
    c->stats.replies++;
    c->sink.send_reply(c->sink.ctx, frame, n);
}

static void send_status(ch9329_core_t *c, uint8_t cmd, uint8_t status)
{
    const uint8_t flag = status == CH9329_STATUS_OK ? CH9329_REPLY_OK_FLAG : CH9329_REPLY_ERR_FLAG;
    send_frame(c, (uint8_t)(cmd | flag), &status, 1u);
}

typedef enum { ROUTE_IGNORE, ROUTE_SILENT, ROUTE_REPLY } route_t;

static route_t route(const ch9329_core_t *c, uint8_t frame_addr)
{
    if (frame_addr == CH9329_ADDR_BROADCAST) {
        return ROUTE_SILENT;
    }
    const uint8_t own = ch9329_cfg_address(c->active.cfg);
    if (own == 0x00u || frame_addr == own) {
        return ROUTE_REPLY;
    }
    return ROUTE_IGNORE;
}

/* ---- command handlers -------------------------------------------------------------------- */

typedef struct {
    uint8_t data[CH9329_MAX_DATA];
    size_t len; /* > 0: data reply; 0: 1-byte status reply */
    bool restart;
} response_t;

static bool link_ready(ch9329_core_t *c)
{
    return c->sink.link_ready != NULL && c->sink.link_ready(c->sink.ctx);
}

static uint8_t work_mode(const ch9329_core_t *c)
{
    return ch9329_cfg_work_mode(c->active.cfg);
}

static bool mode_has_keyboard(const ch9329_core_t *c)
{
    return work_mode(c) != CH9329_WORK_MODE_MOUSE;
}

static bool mode_has_media(const ch9329_core_t *c)
{
    return work_mode(c) == CH9329_WORK_MODE_COMPOSITE || work_mode(c) == CH9329_WORK_MODE_CUSTOM;
}

static bool mode_has_mouse(const ch9329_core_t *c)
{
    return work_mode(c) != CH9329_WORK_MODE_KEYBOARD;
}

/* The HID report used -127..127 (logical minimum -127): map -128 to -127 instead of letting the
 * iPhone drop an out-of-range report. */
static uint8_t clamp_i8(uint8_t v)
{
    return v == 0x80u ? 0x81u : v;
}

/* Normalise a sink answer: anything but OK (including an unexpected value) is reported as E6. */
static uint8_t hid_result(ch9329_core_t *c, uint8_t status)
{
    if (status != CH9329_STATUS_OK) {
        c->stats.hid_failed++;
        return CH9329_STATUS_EXEC_FAILED;
    }
    c->stats.hid_sent++;
    return CH9329_STATUS_OK;
}

static uint8_t cmd_kb_general(ch9329_core_t *c, const uint8_t *d, uint8_t len)
{
    if (len != 8u) {
        return CH9329_STATUS_BAD_PARAM;
    }
    if (!mode_has_keyboard(c) || c->sink.keyboard_report == NULL) {
        return hid_result(c, CH9329_STATUS_EXEC_FAILED);
    }
    uint8_t report[CH9329_KB_REPORT_LEN];
    report[0] = d[0];
    report[1] = 0x00; /* reserved byte: always 0 in the report, whatever the host sent */
    memcpy(&report[2], &d[2], 6u);
    return hid_result(c, c->sink.keyboard_report(c->sink.ctx, report));
}

static uint8_t cmd_kb_media(ch9329_core_t *c, const uint8_t *d, uint8_t len)
{
    const bool acpi = len == 2u && d[0] == CH9329_MEDIA_ACPI;
    const bool media = len == 4u && d[0] == CH9329_MEDIA_MULTIMEDIA;
    if (!acpi && !media) {
        return CH9329_STATUS_BAD_PARAM;
    }
    if (!mode_has_media(c)) {
        return hid_result(c, CH9329_STATUS_EXEC_FAILED);
    }
    if (acpi) {
        if (c->sink.system_report == NULL) {
            return hid_result(c, CH9329_STATUS_EXEC_FAILED);
        }
        const uint8_t report[CH9329_SYSTEM_REPORT_LEN] = {(uint8_t)(d[1] & CH9329_ACPI_MASK)};
        return hid_result(c, c->sink.system_report(c->sink.ctx, report));
    }
    if (c->sink.consumer_report == NULL) {
        return hid_result(c, CH9329_STATUS_EXEC_FAILED);
    }
    /* The consumer report map lists its 24 usages in CH9329 bit order: a straight copy. */
    const uint8_t report[CH9329_CONSUMER_REPORT_LEN] = {d[1], d[2], d[3]};
    return hid_result(c, c->sink.consumer_report(c->sink.ctx, report));
}

uint16_t ch9329_abs_scale(uint16_t grid)
{
    if (grid >= CH9329_ABS_GRID_MAX) {
        return 32767u;
    }
    /* Round to nearest: 0 -> 0, 4095 -> 32767, monotonic. */
    return (uint16_t)(((uint32_t)grid * 32767u + CH9329_ABS_GRID_MAX / 2u) / CH9329_ABS_GRID_MAX);
}

static uint8_t cmd_ms_abs(ch9329_core_t *c, const uint8_t *d, uint8_t len)
{
    if (len != 7u || d[0] != 0x02u) {
        return CH9329_STATUS_BAD_PARAM;
    }
    const uint16_t x = (uint16_t)(d[2] | (uint16_t)(d[3] << 8));
    const uint16_t y = (uint16_t)(d[4] | (uint16_t)(d[5] << 8));
    if (x > CH9329_ABS_GRID_MAX || y > CH9329_ABS_GRID_MAX) {
        return CH9329_STATUS_BAD_PARAM;
    }
    if (!mode_has_mouse(c)) {
        return hid_result(c, CH9329_STATUS_EXEC_FAILED);
    }
    if (c->sink.abs_mouse_report == NULL) {
        if (c->opt.abs_mouse_reject) {
            return CH9329_STATUS_BAD_PARAM;
        }
        /* Built without an absolute pointer collection: documented accept-and-drop. */
        c->stats.abs_dropped++;
        return CH9329_STATUS_OK;
    }
    const uint16_t sx = ch9329_abs_scale(x);
    const uint16_t sy = ch9329_abs_scale(y);
    const uint8_t report[CH9329_ABS_REPORT_LEN] = {
        (uint8_t)(d[1] & 0x07u), (uint8_t)(sx & 0xFFu), (uint8_t)(sx >> 8),
        (uint8_t)(sy & 0xFFu),   (uint8_t)(sy >> 8),    clamp_i8(d[6]),
    };
    return hid_result(c, c->sink.abs_mouse_report(c->sink.ctx, report));
}

static uint8_t cmd_ms_rel(ch9329_core_t *c, const uint8_t *d, uint8_t len)
{
    if (len != 5u || d[0] != 0x01u) {
        return CH9329_STATUS_BAD_PARAM;
    }
    if (!mode_has_mouse(c) || c->sink.mouse_report == NULL) {
        return hid_result(c, CH9329_STATUS_EXEC_FAILED);
    }
    const uint8_t report[CH9329_MOUSE_REPORT_LEN] = {
        (uint8_t)(d[1] & 0x07u), clamp_i8(d[2]), clamp_i8(d[3]), clamp_i8(d[4]),
    };
    return hid_result(c, c->sink.mouse_report(c->sink.ctx, report));
}

/* GET_INFO byte 5 with a clock: the run, its 0.25 ms interval unit and its E7 status. */
#define REL_RUN_FEATURES (CH9329_FEATURE_REL_RUN | CH9329_FEATURE_REL_RUN_QUARTER_MS | CH9329_FEATURE_REL_RUN_LATE)

static bool rel_run_supported(const ch9329_core_t *c)
{
    return c->sink.clock_us != NULL && c->sink.sleep_until_us != NULL;
}

static uint8_t cmd_ms_rel_run(ch9329_core_t *c, const uint8_t *d, uint8_t len)
{
    if (!rel_run_supported(c)) {
        return CH9329_STATUS_BAD_CMD;
    }
    if (len != 5u) {
        return CH9329_STATUS_BAD_PARAM;
    }
    const uint8_t count = d[2];
    const uint8_t flags = d[4];
    const uint32_t unit_us = (flags & CH9329_REL_RUN_QUARTER_MS) != 0u ? 250u : 1000u;
    const uint32_t interval_us = (uint32_t)d[3] * unit_us; /* <= 255 ms */
    if (count == 0u || (flags & CH9329_REL_RUN_RESERVED) != 0u ||
        (uint32_t)(count - 1u) * interval_us > CH9329_REL_RUN_MAX_MS * 1000u) {
        return CH9329_STATUS_BAD_PARAM;
    }
    if (!mode_has_mouse(c) || c->sink.mouse_report == NULL) {
        return hid_result(c, CH9329_STATUS_EXEC_FAILED);
    }
    const uint8_t report[CH9329_MOUSE_REPORT_LEN] = {(uint8_t)(flags & CH9329_REL_RUN_BUTTONS), clamp_i8(d[0]),
                                                     clamp_i8(d[1]), 0x00};
    const uint32_t late_limit = c->opt.run_late_us != 0u ? c->opt.run_late_us : CH9329_RUN_LATE_DEFAULT_US;
    bool late = false;
    const uint32_t t0 = c->sink.clock_us(c->sink.ctx);
    for (uint32_t i = 0; i < count; i++) {
        const uint32_t slot = t0 + i * interval_us; /* count * interval_us < 2^26: no overflow */
        if (i > 0u) {
            /* Absolute schedule: a slow delivery delays the next report, it never shifts the
             * following ones (and a late step is sent at once, not skipped). */
            c->sink.sleep_until_us(c->sink.ctx, slot);
        }
        if (hid_result(c, c->sink.mouse_report(c->sink.ctx, report)) != CH9329_STATUS_OK) {
            return CH9329_STATUS_EXEC_FAILED; /* stop at the first undelivered report */
        }
        const uint32_t at =
            c->sink.accepted_us != NULL ? c->sink.accepted_us(c->sink.ctx) : c->sink.clock_us(c->sink.ctx);
        const int32_t lateness = (int32_t)(at - slot); /* wrap-safe; early (< 0) is on time */
        if (lateness > 0 && (uint32_t)lateness > c->stats.late_max_us) {
            c->stats.late_max_us = (uint32_t)lateness;
        }
        if (lateness > 0 && (uint32_t)lateness > late_limit) {
            late = true; /* keep going: the move completes, only its timing is flagged */
        }
    }
    /* The run owns `count` slots: the reply (and the next frame) wait for the end of the last
     * one, so runs sent back to back continue the same fixed-rate schedule. */
    c->sink.sleep_until_us(c->sink.ctx, t0 + (uint32_t)count * interval_us);
    c->stats.runs++;
    if (late) {
        c->stats.runs_late++;
        return CH9329_STATUS_RUN_LATE;
    }
    return CH9329_STATUS_OK;
}

static uint8_t store(ch9329_core_t *c, const ch9329_persist_t *next)
{
    if (memcmp(next, &c->stored, sizeof(*next)) == 0) {
        return CH9329_STATUS_OK; /* unchanged: spare the flash */
    }
    if (c->sink.persist_store == NULL || !c->sink.persist_store(c->sink.ctx, next)) {
        return CH9329_STATUS_EXEC_FAILED;
    }
    c->stored = *next;
    return CH9329_STATUS_OK;
}

static uint8_t cmd_set_cfg(ch9329_core_t *c, const uint8_t *d, uint8_t len)
{
    if (len != CH9329_CFG_SIZE || !ch9329_cfg_valid(d)) {
        return CH9329_STATUS_BAD_PARAM;
    }
    ch9329_persist_t next = c->stored;
    memcpy(next.cfg, d, CH9329_CFG_SIZE);
    return store(c, &next);
}

static uint8_t cmd_get_string(ch9329_core_t *c, const uint8_t *d, uint8_t len, response_t *r)
{
    if (len != 1u || d[0] >= CH9329_STR_COUNT) {
        return CH9329_STATUS_BAD_PARAM;
    }
    const ch9329_string_t *s = &c->stored.str[d[0]];
    const size_t slen = s->len <= CH9329_STR_MAX ? s->len : CH9329_STR_MAX;
    r->data[0] = d[0];
    r->data[1] = (uint8_t)slen;
    memcpy(&r->data[2], s->text, slen);
    r->len = 2u + slen;
    return CH9329_STATUS_OK;
}

static uint8_t cmd_set_string(ch9329_core_t *c, const uint8_t *d, uint8_t len)
{
    if (len < 2u || d[0] >= CH9329_STR_COUNT || d[1] > CH9329_STR_MAX || (size_t)d[1] + 2u != len ||
        !string_bytes_valid(&d[2], d[1])) {
        return CH9329_STATUS_BAD_PARAM;
    }
    ch9329_persist_t next = c->stored;
    ch9329_string_t *s = &next.str[d[0]];
    memset(s, 0, sizeof(*s));
    s->len = d[1];
    memcpy(s->text, &d[2], d[1]);
    return store(c, &next);
}

static uint8_t dispatch(ch9329_core_t *c, uint8_t cmd, const uint8_t *d, uint8_t len, response_t *r)
{
    switch (cmd) {
    case CH9329_CMD_GET_INFO: {
        if (len != 0u) {
            return CH9329_STATUS_BAD_PARAM;
        }
        const uint8_t leds = c->sink.leds != NULL ? c->sink.leds(c->sink.ctx) : 0u;
        const uint16_t period = c->sink.report_period != NULL ? c->sink.report_period(c->sink.ctx) : 0u;
        memset(r->data, 0, 8u);
        r->data[0] = CH9329_BRIDGE_VERSION;
        r->data[1] = link_ready(c) ? 0x01u : 0x00u;
        r->data[2] = (uint8_t)(leds & 0x07u);
        r->data[3] = c->output;
        r->data[4] = c->collections;
        r->data[5] = rel_run_supported(c) ? REL_RUN_FEATURES : 0x00u;
        r->data[6] = (uint8_t)(period & 0xFFu); /* little-endian, 0.25 ms units */
        r->data[7] = (uint8_t)(period >> 8);
        r->len = 8u;
        return CH9329_STATUS_OK;
    }
    case CH9329_CMD_SEND_KB_GENERAL_DATA:
        return cmd_kb_general(c, d, len);
    case CH9329_CMD_SEND_KB_MEDIA_DATA:
        return cmd_kb_media(c, d, len);
    case CH9329_CMD_SEND_MS_ABS_DATA:
        return cmd_ms_abs(c, d, len);
    case CH9329_CMD_SEND_MS_REL_DATA:
        return cmd_ms_rel(c, d, len);
    case CH9329_CMD_GET_PARA_CFG:
        if (len != 0u) {
            return CH9329_STATUS_BAD_PARAM;
        }
        memcpy(r->data, c->stored.cfg, CH9329_CFG_SIZE);
        r->len = CH9329_CFG_SIZE;
        return CH9329_STATUS_OK;
    case CH9329_CMD_SET_PARA_CFG:
        return cmd_set_cfg(c, d, len);
    case CH9329_CMD_GET_USB_STRING:
        return cmd_get_string(c, d, len, r);
    case CH9329_CMD_SET_USB_STRING:
        return cmd_set_string(c, d, len);
    case CH9329_CMD_SET_DEFAULT_CFG: {
        if (len != 0u) {
            return CH9329_STATUS_BAD_PARAM;
        }
        ch9329_persist_t next;
        ch9329_persist_defaults(&next);
        return store(c, &next);
    }
    case CH9329_CMD_RESET:
        if (len != 0u) {
            return CH9329_STATUS_BAD_PARAM;
        }
        r->restart = true;
        return CH9329_STATUS_OK;
    case CH9329_CMD_SEND_MS_REL_RUN:
        return cmd_ms_rel_run(c, d, len);
    case CH9329_CMD_SEND_MY_HID_DATA: /* custom HID: no such interface on the bridge */
    default:
        return CH9329_STATUS_BAD_CMD;
    }
}

static void handle_frame(ch9329_core_t *c, const ch9329_event_t *ev)
{
    const route_t rt = route(c, ev->addr);
    if (rt == ROUTE_IGNORE) {
        c->stats.ignored_addr++;
        return;
    }
    response_t r;
    r.len = 0;
    r.restart = false;
    const uint8_t status = dispatch(c, ev->cmd, ev->data, ev->len, &r);
    if (rt == ROUTE_REPLY) {
        if (status != CH9329_STATUS_OK) {
            send_status(c, ev->cmd, status);
        } else if (r.len > 0u) {
            send_frame(c, (uint8_t)(ev->cmd | CH9329_REPLY_OK_FLAG), r.data, r.len);
        } else {
            send_status(c, ev->cmd, CH9329_STATUS_OK);
        }
    } else {
        c->stats.broadcast++;
    }
    /* After the reply has been handed to the transport, so the host sees the ack first. */
    if (r.restart && status == CH9329_STATUS_OK && c->sink.request_restart != NULL) {
        c->sink.request_restart(c->sink.ctx);
    }
}

static void on_event(void *user, const ch9329_event_t *ev)
{
    ch9329_core_t *c = (ch9329_core_t *)user;
    switch (ev->type) {
    case CH9329_EV_FRAME:
        handle_frame(c, ev);
        break;
    case CH9329_EV_BAD_SUM:
        if (route(c, ev->addr) == ROUTE_REPLY) {
            send_status(c, ev->cmd, CH9329_STATUS_BAD_SUM);
        }
        break;
    case CH9329_EV_TIMEOUT:
        /* Only answerable once ADDR and CMD are known. */
        if (ev->have >= 4u && route(c, ev->addr) == ROUTE_REPLY) {
            send_status(c, ev->cmd, CH9329_STATUS_TIMEOUT);
        }
        break;
    default:
        break;
    }
}

/* ---- public API -------------------------------------------------------------------------- */

static void update_timeout(ch9329_core_t *c)
{
    uint32_t interval = ch9329_cfg_u16(c->active.cfg, CH9329_CFG_OFF_PACKET_INTERVAL);
    if (interval == 0u) {
        interval = 1u; /* 0 would expire every frame between two bytes */
    }
    c->timeout_ms = interval + c->opt.rx_slack_ms;
}

void ch9329_core_init(ch9329_core_t *c, const ch9329_sink_t *sink, const ch9329_options_t *opt)
{
    memset(c, 0, sizeof(*c));
    if (sink != NULL) {
        c->sink = *sink;
    }
    if (opt != NULL) {
        c->opt = *opt;
    }
    ch9329_parser_init(&c->parser);

    ch9329_persist_t loaded;
    memset(&loaded, 0, sizeof(loaded));
    const bool ok = c->sink.persist_load != NULL && c->sink.persist_load(c->sink.ctx, &loaded) &&
                    ch9329_cfg_valid(loaded.cfg) && ch9329_strings_valid(&loaded);
    if (ok) {
        /* Zero whatever follows each string's NUL so byte-wise comparisons (store()) are exact. */
        for (size_t k = 0; k < CH9329_STR_COUNT; k++) {
            ch9329_string_t *s = &loaded.str[k];
            memset(&s->text[s->len], 0, sizeof(s->text) - s->len);
        }
        c->stored = loaded;
    } else {
        ch9329_persist_defaults(&c->stored);
        c->used_defaults = true;
    }
    c->active = c->stored;
    update_timeout(c);
}

void ch9329_core_set_link_info(ch9329_core_t *c, uint8_t output, uint8_t collections)
{
    c->output = output;
    c->collections = collections;
}

void ch9329_core_set_rx_slack(ch9329_core_t *c, uint32_t slack_ms)
{
    c->opt.rx_slack_ms = slack_ms;
    update_timeout(c);
}

void ch9329_core_feed(ch9329_core_t *c, const uint8_t *data, size_t len, uint32_t now_ms)
{
    ch9329_parser_feed(&c->parser, data, len, now_ms, c->timeout_ms, on_event, c);
}

void ch9329_core_poll(ch9329_core_t *c, uint32_t now_ms)
{
    ch9329_parser_poll(&c->parser, now_ms, c->timeout_ms, on_event, c);
}

uint32_t ch9329_core_ms_until_timeout(const ch9329_core_t *c, uint32_t now_ms)
{
    return ch9329_parser_ms_until_timeout(&c->parser, now_ms, c->timeout_ms);
}
