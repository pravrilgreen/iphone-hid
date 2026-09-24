/*
 * Host tests for the CH9329 core: parser, dispatcher, configuration, address filter, timeouts.
 *
 * Frames in this file are literal hex. The WCH document's examples are used verbatim; reply
 * checksums were computed by hand and are also re-checked by fake_sink.c's independent
 * frame_ok() for every reply the core sends.
 */
#include <stdlib.h>
#include <string.h>

#include "ch9329_proto.h"
#include "fake_sink.h"
#include "tinytest.h"

/* ---- rig --------------------------------------------------------------------------------- */

typedef struct {
    fake_t fake;
    ch9329_core_t core;
    ch9329_options_t opt;
} rig_t;

static rig_t R; /* one rig at a time; large structs stay off the stack */

static void boot(void)
{
    ch9329_sink_t sink = fake_sink(&R.fake);
    ch9329_core_init(&R.core, &sink, &R.opt);
}

static void rig_init(void)
{
    memset(&R, 0, sizeof(R));
    fake_init(&R.fake);
    boot();
}

/* Simulated reboot: flash survives, recorded events are cleared. */
static void reboot(void)
{
    fake_clear(&R.fake);
    boot();
}

static void feed_hex(const char *hex, uint32_t now)
{
    uint8_t buf[512];
    size_t n = hex_bytes(hex, buf, sizeof(buf));
    ch9329_core_feed(&R.core, buf, n, now);
}

static void feed_bytes(const uint8_t *b, size_t n, uint32_t now)
{
    ch9329_core_feed(&R.core, b, n, now);
}

static size_t replies(void)
{
    return fake_count(&R.fake, FK_REPLY);
}

#define CHECK_REPLY(i, hex)                                                                           \
    do {                                                                                              \
        uint8_t exp_[128];                                                                            \
        size_t exp_n_ = hex_bytes((hex), exp_, sizeof(exp_));                                         \
        const fake_event_t *ev_ = fake_nth(&R.fake, FK_REPLY, (i));                                   \
        CHECK(ev_ != NULL);                                                                           \
        if (ev_ != NULL) {                                                                            \
            CHECK_EQ(ev_->len, exp_n_);                                                               \
            CHECK_MEM(ev_->bytes, exp_, exp_n_ < ev_->len ? exp_n_ : ev_->len);                       \
        }                                                                                             \
    } while (0)

#define CHECK_REPORT(kind, i, hex)                                                                    \
    do {                                                                                              \
        uint8_t exp_[16];                                                                             \
        size_t exp_n_ = hex_bytes((hex), exp_, sizeof(exp_));                                         \
        const fake_event_t *ev_ = fake_nth(&R.fake, (kind), (i));                                     \
        CHECK(ev_ != NULL);                                                                           \
        if (ev_ != NULL) {                                                                            \
            CHECK_EQ(ev_->len, exp_n_);                                                               \
            CHECK_MEM(ev_->bytes, exp_, exp_n_);                                                      \
        }                                                                                             \
    } while (0)

/* Reply status byte of the i-th reply, or -1. */
static int reply_status(size_t i)
{
    const fake_event_t *e = fake_nth(&R.fake, FK_REPLY, i);
    if (e == NULL || e->len != 7) {
        return -1;
    }
    return e->bytes[5];
}

static int reply_cmd(size_t i)
{
    const fake_event_t *e = fake_nth(&R.fake, FK_REPLY, i);
    return e == NULL ? -1 : e->bytes[3];
}

/* Encode a request with the core's own encoder (verified separately against WCH vectors). */
static void send_cmd(uint8_t addr, uint8_t cmd, const uint8_t *data, size_t len, uint32_t now)
{
    uint8_t f[CH9329_MAX_FRAME];
    size_t n = ch9329_encode(f, sizeof(f), addr, cmd, data, len);
    feed_bytes(f, n, now);
}

static void set_cfg_bytes(uint8_t cfg[CH9329_CFG_SIZE], uint32_t now)
{
    send_cmd(0x00, CH9329_CMD_SET_PARA_CFG, cfg, CH9329_CFG_SIZE, now);
}

/* Configure flash as if a previous SET_PARA_CFG stored `mutate(defaults)`, then reboot. */
static void boot_with_cfg(uint8_t work_mode, uint8_t address)
{
    ch9329_persist_defaults(&R.fake.flash);
    R.fake.flash.cfg[CH9329_CFG_OFF_WORK_MODE] = work_mode;
    R.fake.flash.cfg[CH9329_CFG_OFF_ADDRESS] = address;
    R.fake.flash_valid = true;
    reboot();
}

/* ---- WCH document vectors ---------------------------------------------------------------- */

static const char *const V_GET_INFO = "57 AB 00 01 00 03";
static const char *const V_PRESS_A = "57 AB 00 02 08 00 00 04 00 00 00 00 00 10";
static const char *const V_RELEASE = "57 AB 00 02 08 00 00 00 00 00 00 00 00 0C";
static const char *const V_SHIFT_A = "57 AB 00 02 08 02 00 04 00 00 00 00 00 12";
static const char *const V_MUTE = "57 AB 00 03 04 02 04 00 00 0F";
static const char *const V_REL_LEFT_PRESS = "57 AB 00 05 05 01 01 00 00 00 0E";
static const char *const V_REL_LEFT_3PX = "57 AB 00 05 05 01 00 FD 00 00 0A";
static const char *const V_ABS_MOVE = "57 AB 00 04 07 02 00 40 01 15 02 00 67";

/* Replies (success = CMD|0x80 + status 00; SUM by hand). */
/* GET_INFO: version 0x40 (bridge), link, LEDs, output 0 (not set), collections 0, features
 * bit0 (the fake sink has a clock: SEND_MS_REL_RUN supported), report period 0 (unknown). */
static const char *const R_INFO_READY = "57 AB 00 81 08 40 01 00 00 00 01 00 00 CD";
static const char *const R_INFO_IDLE = "57 AB 00 81 08 40 00 00 00 00 01 00 00 CC";
static const char *const R_KB_OK = "57 AB 00 82 01 00 85";
static const char *const R_MEDIA_OK = "57 AB 00 83 01 00 86";
static const char *const R_ABS_OK = "57 AB 00 84 01 00 87";
static const char *const R_REL_OK = "57 AB 00 85 01 00 88";

TEST(test_encoder_matches_wch_vectors)
{
    uint8_t f[CH9329_MAX_FRAME], exp[CH9329_MAX_FRAME];
    const uint8_t a[8] = {0, 0, 0x04, 0, 0, 0, 0, 0};
    size_t n = ch9329_encode(f, sizeof(f), 0x00, 0x02, a, sizeof(a));
    size_t m = hex_bytes(V_PRESS_A, exp, sizeof(exp));
    CHECK_EQ(n, m);
    CHECK_MEM(f, exp, m);
    n = ch9329_encode(f, sizeof(f), 0x00, 0x01, NULL, 0);
    m = hex_bytes(V_GET_INFO, exp, sizeof(exp));
    CHECK_EQ(n, m);
    CHECK_MEM(f, exp, m);
    /* Every WCH vector's own checksum agrees with ch9329_checksum(). */
    const char *vecs[] = {V_GET_INFO, V_PRESS_A, V_RELEASE, V_SHIFT_A, V_MUTE, V_REL_LEFT_PRESS, V_REL_LEFT_3PX,
                          V_ABS_MOVE};
    for (size_t i = 0; i < sizeof(vecs) / sizeof(vecs[0]); i++) {
        m = hex_bytes(vecs[i], exp, sizeof(exp));
        CHECK_EQ(ch9329_checksum(exp, m - 1), exp[m - 1]);
        CHECK(frame_ok(exp, m) == false); /* requests have no reply flag */
    }
    /* Too long / too small buffers are refused. */
    uint8_t big[65] = {0};
    CHECK_EQ(ch9329_encode(f, sizeof(f), 0, 1, big, 65), 0);
    CHECK_EQ(ch9329_encode(f, 6, 0, 1, big, 1), 0);
}

TEST(test_get_info)
{
    rig_init();
    feed_hex(V_GET_INFO, 0);
    CHECK_EQ(replies(), 1);
    CHECK_REPLY(0, R_INFO_READY);

    R.fake.ready = false;
    feed_hex(V_GET_INFO, 10);
    CHECK_REPLY(1, R_INFO_IDLE);

    /* LEDs: only bits 0-2 are reported. */
    R.fake.ready = true;
    R.fake.leds = 0xFF;
    feed_hex(V_GET_INFO, 20);
    CHECK_REPLY(2, "57 AB 00 81 08 40 01 07 00 00 01 00 00 D4");
    CHECK_EQ(R.fake.bad_replies, 0);
}

TEST(test_keyboard_vectors)
{
    rig_init();
    feed_hex(V_PRESS_A, 0);
    feed_hex(V_RELEASE, 1);
    feed_hex(V_SHIFT_A, 2);
    CHECK_EQ(fake_count(&R.fake, FK_KB), 3);
    CHECK_REPORT(FK_KB, 0, "00 00 04 00 00 00 00 00");
    CHECK_REPORT(FK_KB, 1, "00 00 00 00 00 00 00 00");
    CHECK_REPORT(FK_KB, 2, "02 00 04 00 00 00 00 00");
    CHECK_EQ(replies(), 3);
    for (size_t i = 0; i < 3; i++) {
        CHECK_REPLY(i, R_KB_OK);
    }
    /* Report is sent before the ack. */
    CHECK(R.fake.ev[0].kind == FK_KB && R.fake.ev[1].kind == FK_REPLY);
}

TEST(test_media_vector)
{
    rig_init();
    feed_hex(V_MUTE, 0);
    CHECK_EQ(fake_count(&R.fake, FK_CONSUMER), 1);
    CHECK_REPORT(FK_CONSUMER, 0, "04 00 00");
    CHECK_REPLY(0, R_MEDIA_OK);
}

TEST(test_media_every_bit_maps_to_same_bit)
{
    rig_init();
    for (unsigned bit = 0; bit < 24; bit++) {
        uint8_t d[4] = {0x02, 0, 0, 0};
        d[1 + bit / 8] = (uint8_t)(1u << (bit % 8));
        send_cmd(0, CH9329_CMD_SEND_KB_MEDIA_DATA, d, sizeof(d), bit);
        const fake_event_t *e = fake_nth(&R.fake, FK_CONSUMER, bit);
        CHECK(e != NULL);
        if (e != NULL) {
            CHECK_MEM(e->bytes, &d[1], 3);
        }
    }
    CHECK_EQ(replies(), 24);
    CHECK_EQ(R.fake.bad_replies, 0);
}

TEST(test_acpi)
{
    rig_init();
    feed_hex("57 AB 00 03 02 01 01 09", 0); /* power */
    CHECK_EQ(fake_count(&R.fake, FK_SYSTEM), 1);
    CHECK_REPORT(FK_SYSTEM, 0, "01");
    CHECK_REPLY(0, R_MEDIA_OK);
    const uint8_t all[2] = {0x01, 0xFF};
    send_cmd(0, CH9329_CMD_SEND_KB_MEDIA_DATA, all, 2, 1);
    CHECK_REPORT(FK_SYSTEM, 1, "07"); /* undefined bits masked */
}

TEST(test_mouse_vectors)
{
    rig_init();
    feed_hex(V_REL_LEFT_PRESS, 0);
    feed_hex(V_REL_LEFT_3PX, 1);
    CHECK_EQ(fake_count(&R.fake, FK_MOUSE), 2);
    CHECK_REPORT(FK_MOUSE, 0, "01 00 00 00");
    CHECK_REPORT(FK_MOUSE, 1, "00 FD 00 00");
    CHECK_REPLY(0, R_REL_OK);
    CHECK_REPLY(1, R_REL_OK);
}

TEST(test_mouse_clamp_and_button_mask)
{
    rig_init();
    const uint8_t d[5] = {0x01, 0xFF, 0x80, 0x7F, 0x80};
    send_cmd(0, CH9329_CMD_SEND_MS_REL_DATA, d, 5, 0);
    CHECK_REPORT(FK_MOUSE, 0, "07 81 7F 81");
}

TEST(test_abs_scale)
{
    CHECK_EQ(ch9329_abs_scale(0), 0);
    CHECK_EQ(ch9329_abs_scale(4095), 32767);
    CHECK_EQ(ch9329_abs_scale(4096), 32767);
    CHECK_EQ(ch9329_abs_scale(0xFFFF), 32767);
    CHECK_EQ(ch9329_abs_scale(2048), 16388);
    uint16_t prev = 0;
    for (uint16_t g = 1; g <= 4095; g++) {
        const uint16_t v = ch9329_abs_scale(g);
        CHECK(v > prev && v - prev <= 9); /* strictly monotonic, no gaps wider than one step */
        prev = v;
    }
}

TEST(test_abs_mouse_forwarded)
{
    rig_init();
    feed_hex(V_ABS_MOVE, 0); /* WCH example: X 0x0140, Y 0x0215 on the 4096 grid */
    CHECK_EQ(fake_count(&R.fake, FK_ABS), 1);
    CHECK_REPORT(FK_ABS, 0, "00 01 0A A9 10 00");
    CHECK_REPLY(0, R_ABS_OK);
    /* Corners, button mask, wheel clamp (-128 -> -127). */
    const uint8_t d[7] = {0x02, 0xFF, 0xFF, 0x0F, 0xFF, 0x0F, 0x80};
    send_cmd(0, CH9329_CMD_SEND_MS_ABS_DATA, d, sizeof(d), 1);
    CHECK_REPORT(FK_ABS, 1, "07 FF 7F FF 7F 81");
    const uint8_t z[7] = {0x02, 0x00, 0x00, 0x00, 0x00, 0x00, 0x01};
    send_cmd(0, CH9329_CMD_SEND_MS_ABS_DATA, z, sizeof(z), 2);
    CHECK_REPORT(FK_ABS, 2, "00 00 00 00 00 01");
    CHECK_EQ(replies(), 3);
    CHECK_EQ(R.core.stats.abs_dropped, 0);
}

TEST(test_abs_mouse_out_of_range_e5)
{
    rig_init();
    const uint8_t bad_x[7] = {0x02, 0x00, 0x00, 0x10, 0x00, 0x00, 0x00}; /* X 4096 */
    const uint8_t bad_y[7] = {0x02, 0x00, 0x00, 0x00, 0xFF, 0xFF, 0x00}; /* Y 65535 */
    send_cmd(0, CH9329_CMD_SEND_MS_ABS_DATA, bad_x, 7, 0);
    send_cmd(0, CH9329_CMD_SEND_MS_ABS_DATA, bad_y, 7, 1);
    CHECK_EQ(reply_status(0), CH9329_STATUS_BAD_PARAM);
    CHECK_EQ(reply_status(1), CH9329_STATUS_BAD_PARAM);
    CHECK_EQ(R.fake.attempts, 0);
}

TEST(test_abs_mouse_link_down_e6)
{
    rig_init();
    R.fake.ready = false;
    feed_hex(V_ABS_MOVE, 0);
    CHECK_REPLY(0, "57 AB 00 C4 01 E6 AD");
    CHECK_EQ(fake_count(&R.fake, FK_ABS), 0);
    R.fake.ready = true;
    R.fake.reject_kinds = 1u << FK_ABS;
    feed_hex(V_ABS_MOVE, 1);
    CHECK_EQ(reply_status(1), CH9329_STATUS_EXEC_FAILED);
}

TEST(test_abs_mouse_without_pointer)
{
    /* A platform built without an absolute pointer: documented accept-and-drop, or E5. */
    memset(&R, 0, sizeof(R));
    fake_init(&R.fake);
    ch9329_sink_t sink = fake_sink(&R.fake);
    sink.abs_mouse_report = NULL;
    ch9329_core_init(&R.core, &sink, NULL);
    feed_hex(V_ABS_MOVE, 0);
    CHECK_REPLY(0, R_ABS_OK);
    CHECK_EQ(R.core.stats.abs_dropped, 1);
    R.fake.ready = false; /* nothing would be sent anyway */
    feed_hex(V_ABS_MOVE, 1);
    CHECK_REPLY(1, R_ABS_OK);

    memset(&R, 0, sizeof(R));
    fake_init(&R.fake);
    sink = fake_sink(&R.fake);
    sink.abs_mouse_report = NULL;
    R.opt.abs_mouse_reject = true;
    ch9329_core_init(&R.core, &sink, &R.opt);
    feed_hex(V_ABS_MOVE, 0);
    CHECK_REPLY(0, "57 AB 00 C4 01 E5 AC");
}

TEST(test_get_info_bridge_bytes)
{
    rig_init();
    ch9329_core_set_link_info(&R.core, CH9329_OUTPUT_USB, 0x1F);
    R.fake.leds = 0x02;
    R.fake.report_period = 4; /* USB, bInterval 1 ms */
    feed_hex(V_GET_INFO, 0);
    CHECK_REPLY(0, "57 AB 00 81 08 40 01 02 02 1F 01 04 00 F4");
}

/* GET_INFO bytes 6-7: the sink's report period, u16 little-endian in 0.25 ms units, asked again
 * on every GET_INFO (a BLE link renegotiates its interval at any time). */
TEST(test_get_info_report_period)
{
    rig_init();
    feed_hex(V_GET_INFO, 0);
    CHECK_REPLY(0, R_INFO_READY); /* default 0: unknown / not connected */

    R.fake.report_period = 60; /* 15 ms connection interval */
    feed_hex(V_GET_INFO, 1);
    CHECK_REPLY(1, "57 AB 00 81 08 40 01 00 00 00 01 3C 00 09");

    R.fake.report_period = 0x1234; /* byte 6 = low byte, byte 7 = high byte */
    feed_hex(V_GET_INFO, 2);
    CHECK_REPLY(2, "57 AB 00 81 08 40 01 00 00 00 01 34 12 13");

    R.fake.report_period = 0xFFFF;
    feed_hex(V_GET_INFO, 3);
    CHECK_REPLY(3, "57 AB 00 81 08 40 01 00 00 00 01 FF FF CB");

    /* Not gated by byte 1: a connected link whose reports are not all deliverable still has
     * its interval. */
    R.fake.report_period = 60;
    R.fake.ready = false;
    feed_hex(V_GET_INFO, 4);
    CHECK_REPLY(4, "57 AB 00 81 08 40 00 00 00 00 01 3C 00 08");

    /* Disconnected: the platform reports 0 again. */
    R.fake.ready = true;
    R.fake.report_period = 0;
    feed_hex(V_GET_INFO, 5);
    CHECK_REPLY(5, R_INFO_READY);

    /* Every value round-trips (the other bytes do not move). */
    fake_clear(&R.fake);
    size_t n = 0;
    for (uint32_t v = 0; v <= 0xFFFFu; v += (v < 512u ? 1u : 251u)) {
        R.fake.report_period = (uint16_t)v;
        feed_hex(V_GET_INFO, 10u + (uint32_t)n);
        const fake_event_t *e = fake_nth(&R.fake, FK_REPLY, 0);
        CHECK(e != NULL && e->len == 14);
        if (e != NULL && e->len == 14) {
            CHECK_EQ((uint32_t)e->bytes[11] | ((uint32_t)e->bytes[12] << 8), v);
            CHECK_EQ(e->bytes[10], CH9329_FEATURE_REL_RUN);
        }
        fake_clear(&R.fake);
        n++;
    }
    CHECK(n > 700);
    CHECK_EQ(R.fake.bad_replies, 0);
}

/* ---- SEND_MS_REL_RUN (vendor 0x30) ------------------------------------------------------- */

static void rel_run(uint8_t addr, int8_t dx, int8_t dy, uint8_t count, uint8_t interval, uint8_t buttons, uint32_t now)
{
    const uint8_t d[5] = {(uint8_t)dx, (uint8_t)dy, count, interval, buttons};
    send_cmd(addr, CH9329_CMD_SEND_MS_REL_RUN, d, sizeof(d), now);
}

TEST(test_rel_run_schedule)
{
    rig_init();
    R.fake.clock = 1000;
    rel_run(0, 5, -3, 4, 15, 0x01, 0);
    CHECK_EQ(fake_count(&R.fake, FK_MOUSE), 4);
    for (size_t i = 0; i < 4; i++) {
        const fake_event_t *e = fake_nth(&R.fake, FK_MOUSE, i);
        CHECK(e != NULL);
        if (e != NULL) {
            CHECK_EQ(e->t, 1000 + 15 * i); /* on the absolute schedule */
            CHECK_MEM(e->bytes, "\x01\x05\xFD\x00", 4);
        }
    }
    CHECK_EQ(R.fake.sleeps, 4); /* 3 between reports, 1 for the last report's slot */
    CHECK_EQ(R.fake.clock, 1060);
    CHECK_EQ(replies(), 1); /* one reply, after the last report's slot */
    CHECK_REPLY(0, "57 AB 00 B0 01 00 B3");
    CHECK(R.fake.ev[R.fake.n - 1].kind == FK_REPLY);
    CHECK_EQ(R.core.stats.runs, 1);
    /* count 1: a single report, no sleep */
    fake_clear(&R.fake);
    rel_run(0, -128, 127, 1, 200, 0, 1);
    CHECK_EQ(fake_count(&R.fake, FK_MOUSE), 1);
    CHECK_REPORT(FK_MOUSE, 0, "00 81 7F 00"); /* -128 clamped to -127 */
    CHECK_EQ(R.fake.sleeps, 1);
}

TEST(test_rel_run_back_to_back_keeps_the_schedule)
{
    /* A move is sent as runs queued behind each other (coarse then fine): the second run's first
     * report comes one interval after the first run's last one, as if it were a single run. */
    rig_init();
    R.fake.clock = 500;
    rel_run(0, 20, 0, 3, 20, 0, 0);
    rel_run(0, 1, 0, 2, 20, 0, 1); /* its bytes arrived while the first run was playing */
    CHECK_EQ(fake_count(&R.fake, FK_MOUSE), 5);
    for (size_t i = 0; i < 5; i++) {
        const fake_event_t *e = fake_nth(&R.fake, FK_MOUSE, i);
        CHECK(e != NULL && e->t == 500 + 20 * i);
    }
    CHECK_REPORT(FK_MOUSE, 2, "00 14 00 00");
    CHECK_REPORT(FK_MOUSE, 3, "00 01 00 00");
    CHECK_EQ(replies(), 2);
    CHECK_EQ(reply_status(0), CH9329_STATUS_OK);
    CHECK_EQ(reply_status(1), CH9329_STATUS_OK);
    CHECK_EQ(R.fake.clock, 600);
}

TEST(test_rel_run_late_steps_sent_not_skipped)
{
    rig_init();
    R.fake.report_cost_ms = 20; /* each report takes longer than the 15 ms interval */
    rel_run(0, 1, 0, 4, 15, 0, 0);
    CHECK_EQ(fake_count(&R.fake, FK_MOUSE), 4);
    const uint32_t want[4] = {20, 40, 60, 80}; /* back to back once late, schedule not shifted */
    for (size_t i = 0; i < 4; i++) {
        const fake_event_t *e = fake_nth(&R.fake, FK_MOUSE, i);
        CHECK(e != NULL && e->t == want[i]);
    }
    CHECK_EQ(reply_status(0), CH9329_STATUS_OK);
}

TEST(test_rel_run_stops_at_first_failure)
{
    rig_init();
    const uint8_t script[] = {CH9329_STATUS_OK, CH9329_STATUS_OK, CH9329_STATUS_EXEC_FAILED};
    fake_script(&R.fake, script, sizeof(script));
    rel_run(0, 3, 3, 10, 5, 0, 0);
    CHECK_EQ(R.fake.attempts, 3); /* nothing after the failed report */
    CHECK_EQ(fake_count(&R.fake, FK_MOUSE), 2);
    CHECK_EQ(replies(), 1);
    CHECK_REPLY(0, "57 AB 00 F0 01 E6 D9");
    CHECK_EQ(R.core.stats.runs, 0);
}

TEST(test_rel_run_parameters)
{
    rig_init();
    rel_run(0, 1, 1, 0, 10, 0, 0);    /* count 0 */
    rel_run(0, 1, 1, 2, 10, 0x08, 1); /* button bit 3 */
    rel_run(0, 1, 1, 255, 8, 0, 2);   /* 254 * 8 = 2032 ms > 2000 */
    const uint8_t short_d[4] = {1, 1, 2, 10};
    send_cmd(0, CH9329_CMD_SEND_MS_REL_RUN, short_d, 4, 3);
    for (size_t i = 0; i < 4; i++) {
        CHECK_EQ(reply_status(i), CH9329_STATUS_BAD_PARAM);
    }
    CHECK_EQ(R.fake.attempts, 0);
    rel_run(0, 1, 0, 201, 10, 0, 4); /* 200 * 10 = 2000 ms: the limit is allowed */
    CHECK_EQ(reply_status(4), CH9329_STATUS_OK);
    CHECK_EQ(fake_count(&R.fake, FK_MOUSE), 201);
    CHECK_EQ(R.fake.clock, 2010); /* 201 slots of 10 ms */
}

TEST(test_rel_run_gating)
{
    /* keyboard-only work mode: E6; broadcast: executed, silent; no clock: E3 */
    rig_init();
    boot_with_cfg(CH9329_WORK_MODE_KEYBOARD, 0);
    rel_run(0, 1, 1, 3, 10, 0, 0);
    CHECK_EQ(reply_status(0), CH9329_STATUS_EXEC_FAILED);
    CHECK_EQ(R.fake.attempts, 0);

    rig_init();
    rel_run(0xFF, 1, 1, 3, 10, 0, 0);
    CHECK_EQ(replies(), 0);
    CHECK_EQ(fake_count(&R.fake, FK_MOUSE), 3);

    memset(&R, 0, sizeof(R));
    fake_init(&R.fake);
    ch9329_sink_t sink = fake_sink(&R.fake);
    sink.clock_ms = NULL;
    sink.sleep_until_ms = NULL;
    ch9329_core_init(&R.core, &sink, NULL);
    rel_run(0, 1, 1, 3, 10, 0, 0);
    CHECK_REPLY(0, "57 AB 00 F0 01 E3 D6");
    CHECK_EQ(R.fake.attempts, 0);
}

TEST(test_kb_reserved_byte_forced_zero)
{
    rig_init();
    const uint8_t d[8] = {0x08, 0x55, 0x2C, 0, 0, 0, 0, 0};
    send_cmd(0, CH9329_CMD_SEND_KB_GENERAL_DATA, d, 8, 0);
    CHECK_REPORT(FK_KB, 0, "08 00 2C 00 00 00 00 00");
}

/* ---- streams ----------------------------------------------------------------------------- */

static const char *const ALL_VECTORS[] = {V_GET_INFO, V_PRESS_A, V_RELEASE, V_SHIFT_A, V_MUTE,
                                          V_REL_LEFT_PRESS, V_REL_LEFT_3PX, V_ABS_MOVE};
static const char *const ALL_REPLIES[] = {R_INFO_READY, R_KB_OK, R_KB_OK, R_KB_OK, R_MEDIA_OK,
                                          R_REL_OK, R_REL_OK, R_ABS_OK};
#define N_VECTORS (sizeof(ALL_VECTORS) / sizeof(ALL_VECTORS[0]))

static size_t all_vectors_stream(uint8_t *out, size_t cap)
{
    size_t n = 0;
    for (size_t i = 0; i < N_VECTORS; i++) {
        n += hex_bytes(ALL_VECTORS[i], out + n, cap - n);
    }
    return n;
}

static void check_all_vector_replies(void)
{
    CHECK_EQ(replies(), N_VECTORS);
    for (size_t i = 0; i < N_VECTORS; i++) {
        CHECK_REPLY(i, ALL_REPLIES[i]);
    }
    CHECK_EQ(fake_count(&R.fake, FK_KB), 3);
    CHECK_EQ(fake_count(&R.fake, FK_CONSUMER), 1);
    CHECK_EQ(fake_count(&R.fake, FK_MOUSE), 2);
    CHECK_EQ(fake_count(&R.fake, FK_ABS), 1);
    CHECK_EQ(R.fake.bad_replies, 0);
}

TEST(test_merged_stream_one_chunk)
{
    rig_init();
    uint8_t s[256];
    size_t n = all_vectors_stream(s, sizeof(s));
    feed_bytes(s, n, 0);
    check_all_vector_replies();
}

TEST(test_split_stream_byte_by_byte)
{
    rig_init();
    uint8_t s[256];
    size_t n = all_vectors_stream(s, sizeof(s));
    for (size_t i = 0; i < n; i++) {
        feed_bytes(&s[i], 1, 0); /* same timestamp: bytes back to back */
    }
    check_all_vector_replies();
}

static uint32_t rng_state = 0x12345678u;
static uint32_t rng(void)
{
    /* xorshift32: deterministic across platforms */
    uint32_t x = rng_state;
    x ^= x << 13;
    x ^= x >> 17;
    x ^= x << 5;
    rng_state = x;
    return x;
}

TEST(test_split_stream_random_chunks)
{
    uint8_t s[256];
    for (int iter = 0; iter < 300; iter++) {
        rig_init();
        size_t n = all_vectors_stream(s, sizeof(s));
        size_t i = 0;
        uint32_t now = 1000;
        while (i < n) {
            size_t chunk = 1 + rng() % 17;
            if (chunk > n - i) {
                chunk = n - i;
            }
            feed_bytes(&s[i], chunk, now);
            now += rng() % 3; /* 0-2 ms between chunks: below the 3 ms packet interval */
            ch9329_core_poll(&R.core, now);
            i += chunk;
        }
        check_all_vector_replies();
        CHECK_EQ(R.core.parser.stats.timeouts, 0);
    }
}

TEST(test_header_split_across_chunks)
{
    rig_init();
    feed_hex("57", 0);
    feed_hex("AB", 1);
    feed_hex("00 01", 2);
    feed_hex("00 03", 3);
    CHECK_EQ(replies(), 1);
    CHECK_REPLY(0, R_INFO_READY);
}

TEST(test_garbage_before_header)
{
    rig_init();
    feed_hex("00 FF 57 12 AB 57 AB 57", 0); /* noise, a false 57, and 57 AB 57 (LEN byte missing) */
    feed_hex("57 AB 00 01 00 03", 0);
    /* The buffered "57 AB 57" plus the real "57 AB" reads as a header with LEN = 0xAB (> 64):
     * it is dropped as a false header and the real frame behind it is found. */
    CHECK_EQ(replies(), 1);
    CHECK_REPLY(0, R_INFO_READY);
    CHECK_EQ(R.core.parser.stats.bad_len, 1);
    CHECK_EQ(R.fake.bad_replies, 0);

    /* Pure ASCII noise (a console log line) never yields a frame. */
    rig_init();
    const char *log = "I (123) bridge: hello W\r\n";
    feed_bytes((const uint8_t *)log, strlen(log), 0);
    feed_hex(V_GET_INFO, 0);
    CHECK_EQ(replies(), 1);
    CHECK_REPLY(0, R_INFO_READY);
    CHECK(R.core.parser.stats.discarded >= strlen(log));
}

TEST(test_bad_checksum_replies_e4)
{
    rig_init();
    feed_hex("57 AB 00 01 00 04", 0);
    CHECK_EQ(replies(), 1);
    CHECK_REPLY(0, "57 AB 00 C1 01 E4 A8");
    /* A keyboard frame with a bad sum is not executed. */
    feed_hex("57 AB 00 02 08 00 00 04 00 00 00 00 00 11", 1);
    CHECK_EQ(fake_count(&R.fake, FK_KB), 0);
    CHECK_REPLY(1, "57 AB 00 C2 01 E4 A9");
    CHECK_EQ(R.core.parser.stats.bad_sum, 2);
    /* Next good frame works. */
    feed_hex(V_PRESS_A, 2);
    CHECK_EQ(fake_count(&R.fake, FK_KB), 1);
    CHECK_REPLY(2, R_KB_OK);
}

TEST(test_lost_last_byte_recovers_next_frame)
{
    rig_init();
    /* PRESS_A without its checksum, immediately followed by GET_INFO: the 57 of GET_INFO is taken
     * as PRESS_A's checksum (mismatch -> E4), then GET_INFO is recovered by the resync. */
    feed_hex("57 AB 00 02 08 00 00 04 00 00 00 00 00", 0);
    feed_hex(V_GET_INFO, 0);
    CHECK_EQ(fake_count(&R.fake, FK_KB), 0);
    CHECK_EQ(replies(), 2);
    CHECK_REPLY(0, "57 AB 00 C2 01 E4 A9");
    CHECK_REPLY(1, R_INFO_READY);
}

TEST(test_len_over_64_resyncs_silently)
{
    rig_init();
    feed_hex("57 AB 00 02 41", 0);
    feed_hex(V_GET_INFO, 0);
    CHECK_EQ(replies(), 1);
    CHECK_REPLY(0, R_INFO_READY);
    CHECK_EQ(R.core.parser.stats.bad_len, 1);

    feed_hex("57 AB 00 09 FF 57 AB 00 05 05 01 00 FD 00 00 0A", 1);
    CHECK_EQ(replies(), 2);
    CHECK_REPLY(1, R_REL_OK);
    CHECK_EQ(R.core.parser.stats.bad_len, 2);

    /* LEN == 64 is legal: a full-size unknown command is answered E3. */
    uint8_t d[64] = {0};
    send_cmd(0, 0x3F, d, 64, 2);
    CHECK_EQ(reply_status(2), CH9329_STATUS_BAD_CMD);
}

TEST(test_max_len_frame_split)
{
    rig_init();
    uint8_t f[CH9329_MAX_FRAME];
    uint8_t d[64];
    memset(d, 0x57, sizeof(d)); /* data full of header bytes must not confuse the parser */
    d[1] = 0xAB;
    size_t n = ch9329_encode(f, sizeof(f), 0, 0x3E, d, 64);
    CHECK_EQ(n, 70);
    feed_bytes(f, 33, 0);
    CHECK_EQ(replies(), 0);
    feed_bytes(f + 33, n - 33, 1);
    CHECK_EQ(replies(), 1);
    CHECK_EQ(reply_cmd(0), 0x3E | 0xC0);
    CHECK_EQ(reply_status(0), CH9329_STATUS_BAD_CMD);
}

/* ---- timeouts ---------------------------------------------------------------------------- */

TEST(test_timeout_replies_e1)
{
    rig_init();
    CHECK_EQ(R.core.timeout_ms, 3);
    CHECK_EQ(ch9329_core_ms_until_timeout(&R.core, 0), UINT32_MAX);
    feed_hex("57 AB 00 02 08 00", 100);
    CHECK_EQ(ch9329_core_ms_until_timeout(&R.core, 100), 3);
    CHECK_EQ(ch9329_core_ms_until_timeout(&R.core, 101), 2);
    ch9329_core_poll(&R.core, 102);
    CHECK_EQ(replies(), 0);
    ch9329_core_poll(&R.core, 103);
    CHECK_EQ(replies(), 1);
    CHECK_REPLY(0, "57 AB 00 C2 01 E1 A6");
    CHECK_EQ(fake_count(&R.fake, FK_KB), 0);
    CHECK_EQ(R.core.parser.n, 0);
    CHECK_EQ(ch9329_core_ms_until_timeout(&R.core, 103), UINT32_MAX);
    /* Polling again does nothing. */
    ch9329_core_poll(&R.core, 200);
    CHECK_EQ(replies(), 1);
    /* The line works afterwards. */
    feed_hex(V_PRESS_A, 300);
    CHECK_REPLY(1, R_KB_OK);
}

TEST(test_timeout_short_partial_is_silent)
{
    rig_init();
    feed_hex("57 AB 00", 0); /* address known, command not */
    ch9329_core_poll(&R.core, 50);
    CHECK_EQ(replies(), 0);
    CHECK_EQ(R.core.parser.stats.timeouts, 1);
    feed_hex("57", 60);
    ch9329_core_poll(&R.core, 70);
    CHECK_EQ(replies(), 0);
    CHECK_EQ(R.core.parser.n, 0);
}

TEST(test_gap_detected_on_feed_without_poll)
{
    rig_init();
    feed_hex("57 AB 00 02 08 00 00", 0);
    /* The rest arrives 10 ms later: the partial expired first (E1), the tail is garbage. */
    feed_hex("04 00 00 00 00 00 10", 10);
    CHECK_EQ(replies(), 1);
    CHECK_REPLY(0, "57 AB 00 C2 01 E1 A6");
    CHECK_EQ(fake_count(&R.fake, FK_KB), 0);
    /* A complete frame after a gap is fine. */
    feed_hex(V_GET_INFO, 20);
    CHECK_REPLY(1, R_INFO_READY);
}

TEST(test_slow_bytes_within_interval)
{
    rig_init();
    uint8_t f[32];
    size_t n = hex_bytes(V_PRESS_A, f, sizeof(f));
    uint32_t now = 0;
    for (size_t i = 0; i < n; i++) {
        feed_bytes(&f[i], 1, now);
        now += 2; /* 2 ms per byte < 3 ms interval */
        ch9329_core_poll(&R.core, now);
    }
    CHECK_EQ(replies(), 1);
    CHECK_REPLY(0, R_KB_OK);
    CHECK_EQ(R.core.parser.stats.timeouts, 0);
}

TEST(test_timeout_recovers_frame_behind_false_header)
{
    rig_init();
    /* A false header claiming LEN 64 swallows a complete GET_INFO; at the timeout the false frame
     * gets E1 and the real one behind it is executed. */
    feed_hex("57 AB 00 01 40", 0);
    feed_hex(V_GET_INFO, 0);
    CHECK_EQ(replies(), 0);
    ch9329_core_poll(&R.core, 3);
    CHECK_EQ(replies(), 2);
    CHECK_REPLY(0, "57 AB 00 C1 01 E1 A5");
    CHECK_REPLY(1, R_INFO_READY);
    CHECK_EQ(R.core.parser.n, 0);
}

TEST(test_timeout_trailing_partial_discarded_once)
{
    rig_init();
    /* False header with a second partial frame inside: only one E1. */
    feed_hex("57 AB 00 01 40 57 AB 00 02 08 00", 0);
    ch9329_core_poll(&R.core, 3);
    CHECK_EQ(replies(), 1);
    CHECK_REPLY(0, "57 AB 00 C1 01 E1 A5");
    ch9329_core_poll(&R.core, 100);
    CHECK_EQ(replies(), 1);
    CHECK_EQ(R.core.parser.n, 0);
}

TEST(test_timeout_wraparound)
{
    rig_init();
    feed_hex("57 AB 00 02 08", UINT32_MAX - 1);
    ch9329_core_poll(&R.core, 0); /* 2 ms later, across the wrap */
    CHECK_EQ(replies(), 0);
    CHECK_EQ(ch9329_core_ms_until_timeout(&R.core, 0), 1);
    ch9329_core_poll(&R.core, 1);
    CHECK_EQ(replies(), 1);
}

TEST(test_rx_slack_and_interval_from_config)
{
    rig_init();
    ch9329_core_set_rx_slack(&R.core, 10);
    CHECK_EQ(R.core.timeout_ms, 13);
    feed_hex("57 AB 00 01", 0);
    ch9329_core_poll(&R.core, 12);
    CHECK_EQ(replies(), 0);
    ch9329_core_poll(&R.core, 13);
    CHECK_EQ(replies(), 1);

    /* packet interval 20 ms stored, then reboot */
    ch9329_persist_defaults(&R.fake.flash);
    ch9329_cfg_put_u16(R.fake.flash.cfg, CH9329_CFG_OFF_PACKET_INTERVAL, 20);
    R.fake.flash_valid = true;
    R.opt.rx_slack_ms = 0;
    reboot();
    CHECK_EQ(R.core.timeout_ms, 20);
    /* interval 0 is treated as 1 ms */
    ch9329_cfg_put_u16(R.fake.flash.cfg, CH9329_CFG_OFF_PACKET_INTERVAL, 0);
    reboot();
    CHECK_EQ(R.core.timeout_ms, 1);
}

TEST(test_char_time)
{
    CHECK_EQ(ch9329_char_time_ms(9600, 1), 2);    /* 1.04 ms rounded up */
    CHECK_EQ(ch9329_char_time_ms(9600, 18), 19);  /* 18.75 */
    CHECK_EQ(ch9329_char_time_ms(115200, 18), 2); /* 1.56 */
    CHECK_EQ(ch9329_char_time_ms(115200, 0), 0);
    CHECK_EQ(ch9329_char_time_ms(0, 10), 0);
    CHECK_EQ(ch9329_char_time_ms(9600, 4000000000u), 4166666667u);
}

/* ---- address filter ---------------------------------------------------------------------- */

TEST(test_address_zero_accepts_any)
{
    rig_init();
    feed_hex("57 AB 33 01 00 36", 0); /* GET_INFO to address 0x33 */
    CHECK_EQ(replies(), 1);
    CHECK_REPLY(0, R_INFO_READY); /* reply carries the bridge's own address (0x00) */
    /* broadcast: executed, silent */
    feed_hex("57 AB FF 02 08 00 00 04 00 00 00 00 00 0F", 1);
    CHECK_EQ(fake_count(&R.fake, FK_KB), 1);
    CHECK_EQ(replies(), 1);
    feed_hex("57 AB FF 01 00 02", 2); /* broadcast GET_INFO: no reply */
    CHECK_EQ(replies(), 1);
    CHECK_EQ(R.core.stats.broadcast, 2);
}

TEST(test_address_filter_nonzero)
{
    rig_init();
    boot_with_cfg(0x00, 0x05);
    send_cmd(0x05, CH9329_CMD_GET_INFO, NULL, 0, 0);
    CHECK_EQ(replies(), 1);
    CHECK_REPLY(0, "57 AB 05 81 08 40 01 00 00 00 01 00 00 D2");
    send_cmd(0x06, CH9329_CMD_GET_INFO, NULL, 0, 1);
    send_cmd(0x00, CH9329_CMD_GET_INFO, NULL, 0, 2);
    const uint8_t kb[8] = {0, 0, 4, 0, 0, 0, 0, 0};
    send_cmd(0x06, CH9329_CMD_SEND_KB_GENERAL_DATA, kb, 8, 3);
    CHECK_EQ(replies(), 1);
    CHECK_EQ(fake_count(&R.fake, FK_KB), 0);
    CHECK_EQ(R.core.stats.ignored_addr, 3);
    send_cmd(0xFF, CH9329_CMD_SEND_KB_GENERAL_DATA, kb, 8, 4);
    CHECK_EQ(fake_count(&R.fake, FK_KB), 1);
    CHECK_EQ(replies(), 1);
    send_cmd(0x05, CH9329_CMD_SEND_KB_GENERAL_DATA, kb, 8, 5);
    CHECK_EQ(fake_count(&R.fake, FK_KB), 2);
    CHECK_REPLY(1, "57 AB 05 82 01 00 8A");
}

TEST(test_errors_respect_address)
{
    rig_init();
    boot_with_cfg(0x00, 0x05);
    feed_hex("57 AB 06 01 00 00", 0); /* bad sum, other address */
    feed_hex("57 AB FF 01 00 00", 1); /* bad sum, broadcast */
    CHECK_EQ(replies(), 0);
    feed_hex("57 AB 05 01 00 00", 2); /* bad sum, ours */
    CHECK_EQ(replies(), 1);
    CHECK_EQ(reply_status(0), CH9329_STATUS_BAD_SUM);
    feed_hex("57 AB 06 02 08", 10); /* partial, other address */
    ch9329_core_poll(&R.core, 20);
    feed_hex("57 AB FF 02 08", 30); /* partial, broadcast */
    ch9329_core_poll(&R.core, 40);
    CHECK_EQ(replies(), 1);
    feed_hex("57 AB 05 02 08", 50);
    ch9329_core_poll(&R.core, 60);
    CHECK_EQ(replies(), 2);
    CHECK_EQ(reply_status(1), CH9329_STATUS_TIMEOUT);
    CHECK_EQ(reply_cmd(1), 0xC2);
    /* unknown command to another address: ignored */
    send_cmd(0x06, 0x30, NULL, 0, 70);
    CHECK_EQ(replies(), 2);
}

/* ---- commands and parameters ------------------------------------------------------------- */

TEST(test_unknown_commands_e3)
{
    rig_init();
    const uint8_t cmds[] = {0x00, 0x06, 0x07, 0x0D, 0x0E, 0x10, 0x3F, 0x40, 0x81};
    for (size_t i = 0; i < sizeof(cmds); i++) {
        send_cmd(0, cmds[i], NULL, 0, (uint32_t)i);
        CHECK_EQ(reply_status(i), CH9329_STATUS_BAD_CMD);
        CHECK_EQ(reply_cmd(i), cmds[i] | 0xC0);
    }
    CHECK_EQ(R.fake.bad_replies, 0);
}

TEST(test_bad_parameters_e5)
{
    rig_init();
    struct {
        uint8_t cmd;
        const char *data;
    } cases[] = {
        {CH9329_CMD_GET_INFO, "00"},
        {CH9329_CMD_SEND_KB_GENERAL_DATA, "00 00 04 00 00 00 00"},
        {CH9329_CMD_SEND_KB_GENERAL_DATA, "00 00 04 00 00 00 00 00 00"},
        {CH9329_CMD_SEND_KB_MEDIA_DATA, "03 01"},
        {CH9329_CMD_SEND_KB_MEDIA_DATA, "02 01 00"},
        {CH9329_CMD_SEND_KB_MEDIA_DATA, "01 01 00 00"},
        {CH9329_CMD_SEND_KB_MEDIA_DATA, "01"},
        {CH9329_CMD_SEND_KB_MEDIA_DATA, ""},
        {CH9329_CMD_SEND_MS_ABS_DATA, "01 00 40 01 15 02 00"},
        {CH9329_CMD_SEND_MS_ABS_DATA, "02 00 40 01 15 02"},
        {CH9329_CMD_SEND_MS_REL_DATA, "02 00 01 00 00"},
        {CH9329_CMD_SEND_MS_REL_DATA, "01 00 01 00"},
        {CH9329_CMD_GET_PARA_CFG, "00"},
        {CH9329_CMD_SET_PARA_CFG, "00"},
        {CH9329_CMD_SET_DEFAULT_CFG, "00"},
        {CH9329_CMD_RESET, "00"},
        {CH9329_CMD_GET_USB_STRING, ""},
        {CH9329_CMD_GET_USB_STRING, "03"},
        {CH9329_CMD_GET_USB_STRING, "00 00"},
    };
    const size_t n = sizeof(cases) / sizeof(cases[0]);
    for (size_t i = 0; i < n; i++) {
        uint8_t d[64];
        size_t len = hex_bytes(cases[i].data, d, sizeof(d));
        send_cmd(0, cases[i].cmd, d, len, (uint32_t)i);
        CHECK_EQ(reply_status(i), CH9329_STATUS_BAD_PARAM);
        CHECK_EQ(reply_cmd(i), cases[i].cmd | 0xC0);
    }
    CHECK_EQ(replies(), n);
    CHECK_EQ(fake_count(&R.fake, FK_KB) + fake_count(&R.fake, FK_MOUSE) + fake_count(&R.fake, FK_CONSUMER) +
                 fake_count(&R.fake, FK_SYSTEM),
             0);
    CHECK_EQ(R.fake.restarts, 0);
    CHECK_EQ(R.fake.stores, 0);
}

TEST(test_link_not_ready_e6)
{
    rig_init();
    R.fake.ready = false;
    feed_hex(V_PRESS_A, 0);
    feed_hex(V_MUTE, 1);
    feed_hex(V_REL_LEFT_PRESS, 2);
    feed_hex("57 AB 00 03 02 01 01 09", 3);
    CHECK_EQ(replies(), 4);
    CHECK_REPLY(0, "57 AB 00 C2 01 E6 AB");
    CHECK_REPLY(1, "57 AB 00 C3 01 E6 AC");
    CHECK_REPLY(2, "57 AB 00 C5 01 E6 AE");
    CHECK_REPLY(3, "57 AB 00 C3 01 E6 AC");
    CHECK_EQ(R.fake.n, 4); /* only the 4 replies: no report was delivered */
    CHECK_EQ(R.fake.attempts, 4); /* the sink decided each time (it knows the link state) */
    CHECK_EQ(R.core.stats.hid_failed, 4);
}

TEST(test_sink_failure_e6)
{
    rig_init();
    R.fake.hid_status = CH9329_STATUS_EXEC_FAILED; /* e.g. BLE out of buffers after the wait */
    feed_hex(V_PRESS_A, 0);
    CHECK_EQ(R.fake.attempts, 1);
    CHECK_EQ(fake_count(&R.fake, FK_KB), 0);
    CHECK_EQ(replies(), 1);
    CHECK_REPLY(0, "57 AB 00 C2 01 E6 AB");
    CHECK_EQ(R.core.stats.hid_failed, 1);
    CHECK_EQ(R.core.stats.hid_sent, 0);
}

TEST(test_sink_unexpected_status_is_e6)
{
    /* A sink answering anything but OK must never turn into an OK (or a misleading code). */
    const uint8_t odd[] = {0x01, 0x42, CH9329_STATUS_BAD_PARAM, CH9329_STATUS_TIMEOUT, 0xFF};
    for (size_t i = 0; i < sizeof(odd); i++) {
        rig_init();
        R.fake.hid_status = odd[i];
        feed_hex(V_REL_LEFT_PRESS, 0);
        CHECK_EQ(replies(), 1);
        CHECK_REPLY(0, "57 AB 00 C5 01 E6 AE");
        CHECK_EQ(fake_count(&R.fake, FK_MOUSE), 0);
    }
}

TEST(test_per_report_delivery)
{
    /* Keyboard deliverable, mouse not (e.g. the phone never subscribed to it): each command gets
     * its own true answer; GET_INFO reports the link as not fully ready. */
    rig_init();
    R.fake.reject_kinds = 1u << FK_MOUSE;
    feed_hex(V_PRESS_A, 0);
    feed_hex(V_REL_LEFT_PRESS, 1);
    feed_hex(V_MUTE, 2);
    CHECK_REPLY(0, R_KB_OK);
    CHECK_REPLY(1, "57 AB 00 C5 01 E6 AE");
    CHECK_REPLY(2, R_MEDIA_OK);
    CHECK_EQ(fake_count(&R.fake, FK_KB), 1);
    CHECK_EQ(fake_count(&R.fake, FK_MOUSE), 0);
    CHECK_EQ(fake_count(&R.fake, FK_CONSUMER), 1);
    /* GET_INFO comes from link_ready, which the platform computes (here: `ready`). */
    R.fake.ready = false;
    feed_hex(V_GET_INFO, 3);
    CHECK_REPLY(3, R_INFO_IDLE);
    /* Link down: even the keyboard is refused, and nothing is recorded as delivered. */
    feed_hex(V_RELEASE, 4);
    CHECK_REPLY(4, "57 AB 00 C2 01 E6 AB");
    CHECK_EQ(fake_count(&R.fake, FK_KB), 1);
}

/* Encode one random HID command of the given class into f; returns its length. */
static size_t random_hid_frame(uint8_t *f, size_t cap, uint32_t r, uint8_t *cmd_out)
{
    switch (r % 5) {
    case 4: {
        const uint16_t x = (uint16_t)((r >> 3) % 4096u), y = (uint16_t)((r >> 15) % 4096u);
        uint8_t d[7] = {0x02, (uint8_t)((r >> 8) & 7u), (uint8_t)x, (uint8_t)(x >> 8), (uint8_t)y, (uint8_t)(y >> 8), 0};
        *cmd_out = CH9329_CMD_SEND_MS_ABS_DATA;
        return ch9329_encode(f, cap, 0, *cmd_out, d, sizeof(d));
    }
    case 0: {
        uint8_t d[8] = {(uint8_t)(r >> 8), 0, (uint8_t)(r >> 16), 0, 0, 0, 0, 0};
        *cmd_out = CH9329_CMD_SEND_KB_GENERAL_DATA;
        return ch9329_encode(f, cap, 0, *cmd_out, d, sizeof(d));
    }
    case 1: {
        uint8_t d[5] = {0x01, (uint8_t)((r >> 8) & 7u), (uint8_t)(r >> 12), (uint8_t)(r >> 20), (uint8_t)(r >> 28)};
        if (d[2] == 0x80) {
            d[2] = 0x7F; /* keep the expected report equal to the request (no clamping here) */
        }
        if (d[3] == 0x80) {
            d[3] = 0x7F;
        }
        *cmd_out = CH9329_CMD_SEND_MS_REL_DATA;
        return ch9329_encode(f, cap, 0, *cmd_out, d, sizeof(d));
    }
    case 2: {
        uint8_t d[4] = {0x02, (uint8_t)(r >> 8), (uint8_t)(r >> 16), (uint8_t)(r >> 24)};
        *cmd_out = CH9329_CMD_SEND_KB_MEDIA_DATA;
        return ch9329_encode(f, cap, 0, *cmd_out, d, sizeof(d));
    }
    default: {
        uint8_t d[2] = {0x01, (uint8_t)((r >> 8) & 7u)};
        *cmd_out = CH9329_CMD_SEND_KB_MEDIA_DATA;
        return ch9329_encode(f, cap, 0, *cmd_out, d, sizeof(d));
    }
    }
}

TEST(test_ok_if_and_only_if_delivered)
{
    /* The product rule: a HID command is answered 00 exactly when its report reached the sink,
     * reports keep the host's order, and every command gets exactly one reply. Random link state,
     * random per-report failures, random chunking of a pipelined stream. */
    rig_init();
    rng_state = 0xBADC0DEu;
    enum { N = 3000 };
    static uint8_t stream[N * 16];
    static uint8_t cmds[N];
    static uint8_t outcome[N];
    size_t len = 0;
    for (size_t i = 0; i < N; i++) {
        const uint32_t r = rng();
        len += random_hid_frame(&stream[len], sizeof(stream) - len, r, &cmds[i]);
        outcome[i] = (rng() % 5u == 0u) ? CH9329_STATUS_EXEC_FAILED : CH9329_STATUS_OK;
    }
    /* Feed in random chunks; before each chunk, script the outcomes of the frames it completes. */
    size_t pos = 0, next_cmd = 0, done_replies = 0, delivered = 0;
    uint32_t now = 0;
    while (pos < len) {
        size_t chunk = 1u + rng() % 40u;
        if (chunk > len - pos) {
            chunk = len - pos;
        }
        fake_clear(&R.fake);
        /* Script enough statuses for any frame completed by this chunk (at most 40 / 7 frames). */
        uint8_t script[16];
        size_t k;
        for (k = 0; k < sizeof(script) && next_cmd + k < N; k++) {
            script[k] = outcome[next_cmd + k];
        }
        fake_script(&R.fake, script, k);
        feed_bytes(&stream[pos], chunk, now);
        pos += chunk;
        /* Walk the events of this chunk: each reply must match the next command's outcome, and
         * an OK reply must be preceded by exactly one report of that command. */
        bool pending_report = false;
        for (size_t e = 0; e < R.fake.n; e++) {
            const fake_event_t *ev = &R.fake.ev[e];
            if (ev->kind == FK_REPLY) {
                CHECK(next_cmd < N);
                if (next_cmd >= N) {
                    break;
                }
                const uint8_t want = outcome[next_cmd];
                CHECK_EQ(ev->len, 7);
                CHECK_EQ(ev->bytes[5], want);
                CHECK_EQ(ev->bytes[3], cmds[next_cmd] | (want == CH9329_STATUS_OK ? 0x80 : 0xC0));
                CHECK(pending_report == (want == CH9329_STATUS_OK));
                pending_report = false;
                next_cmd++;
                done_replies++;
            } else {
                CHECK(!pending_report); /* never two reports for one command */
                pending_report = true;
                delivered++;
            }
        }
        CHECK(!pending_report);
        CHECK_EQ(R.fake.script_pos, R.fake.attempts); /* one sink call per completed frame */
        now += rng() % 2u;                            /* < packet interval: no timeouts */
    }
    CHECK_EQ(next_cmd, N);
    CHECK_EQ(done_replies, N);
    size_t oks = 0;
    for (size_t i = 0; i < N; i++) {
        oks += outcome[i] == CH9329_STATUS_OK;
    }
    CHECK_EQ(delivered, oks);
    CHECK_EQ(R.core.stats.hid_sent, oks);
    CHECK_EQ(R.core.stats.hid_failed, N - oks);
    CHECK_EQ(R.core.parser.stats.timeouts, 0);
}

TEST(test_broadcast_failure_stays_silent)
{
    rig_init();
    R.fake.ready = false;
    feed_hex("57 AB FF 02 08 00 00 04 00 00 00 00 00 0F", 0);
    CHECK_EQ(replies(), 0); /* broadcast is never answered, not even with E6 */
    CHECK_EQ(R.core.stats.hid_failed, 1);
}

TEST(test_null_sink_callbacks)
{
    memset(&R, 0, sizeof(R));
    fake_init(&R.fake);
    ch9329_sink_t s = {.ctx = &R.fake, .send_reply = fake_sink(&R.fake).send_reply};
    ch9329_core_init(&R.core, &s, NULL);
    CHECK(R.core.used_defaults);
    feed_hex(V_GET_INFO, 0);
    /* No clock: no REL_RUN. No report_period: bytes 6-7 are 0. */
    CHECK_REPLY(0, "57 AB 00 81 08 40 00 00 00 00 00 00 00 CB");
    feed_hex(V_PRESS_A, 1);
    CHECK_EQ(reply_status(1), CH9329_STATUS_EXEC_FAILED);
    uint8_t cfg[CH9329_CFG_SIZE];
    memcpy(cfg, R.core.stored.cfg, sizeof(cfg));
    cfg[CH9329_CFG_OFF_WORK_MODE] = 1;
    set_cfg_bytes(cfg, 2);
    CHECK_EQ(reply_status(2), CH9329_STATUS_EXEC_FAILED); /* cannot store */
    feed_hex("57 AB 00 0F 00 11", 3);                     /* RESET without a restart hook */
    CHECK_EQ(reply_status(3), CH9329_STATUS_OK);
    /* A core with no sink at all must not crash. */
    ch9329_core_t bare;
    ch9329_core_init(&bare, NULL, NULL);
    uint8_t f[16];
    size_t n = hex_bytes(V_PRESS_A, f, sizeof(f));
    ch9329_core_feed(&bare, f, n, 0);
    ch9329_core_poll(&bare, 100);
}

/* ---- work modes -------------------------------------------------------------------------- */

TEST(test_work_mode_keyboard_only)
{
    rig_init();
    boot_with_cfg(CH9329_WORK_MODE_KEYBOARD, 0);
    feed_hex(V_PRESS_A, 0);
    feed_hex(V_MUTE, 1);
    feed_hex("57 AB 00 03 02 01 01 09", 2);
    feed_hex(V_REL_LEFT_PRESS, 3);
    feed_hex(V_ABS_MOVE, 4);
    CHECK_REPLY(0, R_KB_OK);
    CHECK_EQ(reply_status(1), CH9329_STATUS_EXEC_FAILED);
    CHECK_EQ(reply_status(2), CH9329_STATUS_EXEC_FAILED);
    CHECK_EQ(reply_status(3), CH9329_STATUS_EXEC_FAILED);
    CHECK_EQ(reply_status(4), CH9329_STATUS_EXEC_FAILED);
    CHECK_EQ(fake_count(&R.fake, FK_KB), 1);
    CHECK_EQ(fake_count(&R.fake, FK_MOUSE) + fake_count(&R.fake, FK_CONSUMER) + fake_count(&R.fake, FK_SYSTEM), 0);
}

TEST(test_work_mode_mouse_only)
{
    rig_init();
    boot_with_cfg(CH9329_WORK_MODE_MOUSE, 0);
    feed_hex(V_PRESS_A, 0);
    feed_hex(V_MUTE, 1);
    feed_hex(V_REL_LEFT_PRESS, 2);
    feed_hex(V_ABS_MOVE, 3);
    CHECK_EQ(reply_status(0), CH9329_STATUS_EXEC_FAILED);
    CHECK_EQ(reply_status(1), CH9329_STATUS_EXEC_FAILED);
    CHECK_REPLY(2, R_REL_OK);
    CHECK_REPLY(3, R_ABS_OK);
    CHECK_EQ(fake_count(&R.fake, FK_KB), 0);
    CHECK_EQ(fake_count(&R.fake, FK_MOUSE), 1);
}

TEST(test_work_mode_custom_acts_composite)
{
    rig_init();
    boot_with_cfg(CH9329_WORK_MODE_CUSTOM, 0);
    uint8_t s[256];
    size_t n = all_vectors_stream(s, sizeof(s));
    feed_bytes(s, n, 0);
    check_all_vector_replies();
}

/* ---- configuration ----------------------------------------------------------------------- */

static const char *const DEFAULT_CFG_HEX =
    "00 00 00 00 00 25 80 00 00 00 03 1A 86 E1 29 00 00 00 01 00 0D 00 00 00 00 00 00 00 00 00 "
    "00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00";

TEST(test_get_default_config)
{
    rig_init();
    CHECK(R.core.used_defaults);
    feed_hex("57 AB 00 08 00 0A", 0);
    CHECK_EQ(replies(), 1);
    const fake_event_t *e = fake_nth(&R.fake, FK_REPLY, 0);
    CHECK(e != NULL && e->len == 56);
    if (e != NULL && e->len == 56) {
        uint8_t exp[50];
        CHECK_EQ(hex_bytes(DEFAULT_CFG_HEX, exp, sizeof(exp)), 50);
        CHECK_EQ(e->bytes[3], 0x88);
        CHECK_EQ(e->bytes[4], 50);
        CHECK_MEM(&e->bytes[5], exp, 50);
    }
    CHECK_EQ(ch9329_cfg_baud(R.core.active.cfg), 9600);
    CHECK(ch9329_cfg_valid(R.core.active.cfg));
}

TEST(test_set_get_config_round_trip)
{
    rig_init();
    uint8_t cfg[CH9329_CFG_SIZE];
    memcpy(cfg, R.core.stored.cfg, sizeof(cfg));
    cfg[CH9329_CFG_OFF_WORK_MODE] = CH9329_WORK_MODE_KEYBOARD;
    cfg[CH9329_CFG_OFF_ADDRESS] = 0x05;
    ch9329_cfg_put_u32(cfg, CH9329_CFG_OFF_BAUD, 115200);
    ch9329_cfg_put_u16(cfg, CH9329_CFG_OFF_PACKET_INTERVAL, 5);
    cfg[45] = 0xA5; /* reserved bytes are stored verbatim */
    set_cfg_bytes(cfg, 0);
    CHECK_REPLY(0, "57 AB 00 89 01 00 8C");
    CHECK_EQ(R.fake.stores, 1);

    feed_hex("57 AB 00 08 00 0A", 1);
    const fake_event_t *e = fake_nth(&R.fake, FK_REPLY, 1);
    CHECK(e != NULL && e->len == 56);
    if (e != NULL && e->len == 56) {
        CHECK_MEM(&e->bytes[5], cfg, 50);
    }
    /* Running config unchanged until reboot: address 0 still answers anything, mouse still works. */
    send_cmd(0x33, CH9329_CMD_GET_INFO, NULL, 0, 2);
    CHECK_REPLY(2, R_INFO_READY);
    feed_hex(V_REL_LEFT_PRESS, 3);
    CHECK_REPLY(3, R_REL_OK);
    CHECK_EQ(ch9329_cfg_baud(R.core.active.cfg), 9600);

    /* Writing the same block again does not touch flash. */
    set_cfg_bytes(cfg, 4);
    CHECK_EQ(reply_status(4), CH9329_STATUS_OK);
    CHECK_EQ(R.fake.stores, 1);

    /* After a reboot the stored values are live. */
    reboot();
    CHECK(!R.core.used_defaults);
    CHECK_EQ(ch9329_cfg_baud(R.core.active.cfg), 115200);
    CHECK_EQ(ch9329_cfg_address(R.core.active.cfg), 0x05);
    CHECK_EQ(R.core.timeout_ms, 5);
    send_cmd(0x33, CH9329_CMD_GET_INFO, NULL, 0, 10);
    CHECK_EQ(replies(), 0);
    send_cmd(0x05, CH9329_CMD_SEND_MS_REL_DATA, (const uint8_t *)"\x01\x00\x01\x00\x00", 5, 11);
    CHECK_EQ(reply_status(0), CH9329_STATUS_EXEC_FAILED); /* keyboard-only mode */
    CHECK_EQ(reply_cmd(0), 0xC5);
}

TEST(test_set_config_validation)
{
    rig_init();
    uint8_t base[CH9329_CFG_SIZE];
    memcpy(base, R.core.stored.cfg, sizeof(base));
    struct {
        size_t off;
        uint8_t val;
    } bad[] = {
        {CH9329_CFG_OFF_WORK_MODE, 0x04},  {CH9329_CFG_OFF_WORK_MODE, 0x80}, {CH9329_CFG_OFF_WORK_MODE, 0xFF},
        {CH9329_CFG_OFF_SERIAL_MODE, 0x01}, {CH9329_CFG_OFF_SERIAL_MODE, 0x02},
        {CH9329_CFG_OFF_SERIAL_MODE, 0x80}, {CH9329_CFG_OFF_ADDRESS, 0xFF},
        {CH9329_CFG_OFF_BAUD, 0x01}, /* 0x01002580: not a supported rate */
    };
    size_t i;
    for (i = 0; i < sizeof(bad) / sizeof(bad[0]); i++) {
        uint8_t cfg[CH9329_CFG_SIZE];
        memcpy(cfg, base, sizeof(cfg));
        cfg[bad[i].off] = bad[i].val;
        set_cfg_bytes(cfg, (uint32_t)i);
        CHECK_EQ(reply_status(i), CH9329_STATUS_BAD_PARAM);
    }
    const uint32_t bauds_bad[] = {0, 1200, 4800, 12345, 230400, 921600};
    for (size_t k = 0; k < sizeof(bauds_bad) / sizeof(bauds_bad[0]); k++, i++) {
        uint8_t cfg[CH9329_CFG_SIZE];
        memcpy(cfg, base, sizeof(cfg));
        ch9329_cfg_put_u32(cfg, CH9329_CFG_OFF_BAUD, bauds_bad[k]);
        set_cfg_bytes(cfg, (uint32_t)i);
        CHECK_EQ(reply_status(i), CH9329_STATUS_BAD_PARAM);
    }
    /* wrong length */
    send_cmd(0, CH9329_CMD_SET_PARA_CFG, base, 49, 100);
    CHECK_EQ(reply_status(i), CH9329_STATUS_BAD_PARAM);
    i++;
    CHECK_EQ(R.fake.stores, 0);
    CHECK_EQ(fake_count(&R.fake, FK_STORE), 0);

    /* every supported rate and work mode 0-3 is accepted; address 0x01-0xFE too */
    const uint32_t bauds_ok[] = {9600, 19200, 38400, 57600, 115200};
    for (size_t k = 0; k < 5; k++, i++) {
        uint8_t cfg[CH9329_CFG_SIZE];
        memcpy(cfg, base, sizeof(cfg));
        ch9329_cfg_put_u32(cfg, CH9329_CFG_OFF_BAUD, bauds_ok[k]);
        cfg[CH9329_CFG_OFF_WORK_MODE] = (uint8_t)(k % 4);
        cfg[CH9329_CFG_OFF_ADDRESS] = (uint8_t)(k == 4 ? 0xFE : k);
        set_cfg_bytes(cfg, (uint32_t)i);
        CHECK_EQ(reply_status(i), CH9329_STATUS_OK);
    }
}

TEST(test_set_config_store_failure)
{
    rig_init();
    R.fake.store_ok = false;
    uint8_t cfg[CH9329_CFG_SIZE];
    memcpy(cfg, R.core.stored.cfg, sizeof(cfg));
    cfg[CH9329_CFG_OFF_WORK_MODE] = 0x02;
    set_cfg_bytes(cfg, 0);
    CHECK_REPLY(0, "57 AB 00 C9 01 E6 B2");
    CHECK_EQ(R.core.stored.cfg[CH9329_CFG_OFF_WORK_MODE], 0x00); /* GET still shows the old block */
}

TEST(test_set_default_config)
{
    rig_init();
    boot_with_cfg(CH9329_WORK_MODE_MOUSE, 0x07);
    strcpy(R.fake.flash.str[CH9329_STR_PRODUCT].text, "X");
    R.fake.flash.str[CH9329_STR_PRODUCT].len = 1;
    reboot();
    CHECK_EQ(R.core.stored.str[CH9329_STR_PRODUCT].len, 1);
    send_cmd(0x07, CH9329_CMD_SET_DEFAULT_CFG, NULL, 0, 0);
    CHECK_REPLY(0, "57 AB 07 8C 01 00 96");
    ch9329_persist_t d;
    ch9329_persist_defaults(&d);
    CHECK_MEM(R.core.stored.cfg, d.cfg, CH9329_CFG_SIZE);
    CHECK_EQ(R.core.stored.str[CH9329_STR_PRODUCT].len, 0);
    CHECK_EQ(R.fake.stores, 1);
    /* still running on the old config until reboot */
    CHECK_EQ(ch9329_cfg_address(R.core.active.cfg), 0x07);
    reboot();
    CHECK_EQ(ch9329_cfg_address(R.core.active.cfg), 0x00);
    CHECK_EQ(ch9329_cfg_work_mode(R.core.active.cfg), 0x00);
}

TEST(test_invalid_flash_falls_back_to_defaults)
{
    rig_init();
    ch9329_persist_defaults(&R.fake.flash);
    R.fake.flash.cfg[CH9329_CFG_OFF_WORK_MODE] = 0x07;
    R.fake.flash_valid = true;
    reboot();
    CHECK(R.core.used_defaults);
    CHECK_EQ(ch9329_cfg_work_mode(R.core.active.cfg), 0);

    ch9329_persist_defaults(&R.fake.flash);
    R.fake.flash.str[1].len = 3;
    memcpy(R.fake.flash.str[1].text, "a\x01z", 4); /* non-printable */
    reboot();
    CHECK(R.core.used_defaults);

    ch9329_persist_defaults(&R.fake.flash);
    R.fake.flash.str[1].len = 30; /* longer than 23 */
    reboot();
    CHECK(R.core.used_defaults);

    ch9329_persist_defaults(&R.fake.flash);
    R.fake.flash.str[2].len = 2;
    memcpy(R.fake.flash.str[2].text, "abc", 4); /* not NUL-terminated at len */
    reboot();
    CHECK(R.core.used_defaults);

    /* garbage after the NUL is tolerated and normalised */
    ch9329_persist_defaults(&R.fake.flash);
    R.fake.flash.str[0].len = 2;
    memcpy(R.fake.flash.str[0].text, "ab\0zz", 5);
    reboot();
    CHECK(!R.core.used_defaults);
    CHECK_EQ(R.core.stored.str[0].text[3], 0);
}

TEST(test_usb_strings)
{
    rig_init();
    feed_hex("57 AB 00 0A 01 01 0E", 0); /* GET product */
    CHECK_REPLY(0, "57 AB 00 8A 02 01 00 8F");
    /* SET product "Rack-A 07" */
    const char *name = "Rack-A 07";
    uint8_t d[2 + 23];
    d[0] = CH9329_STR_PRODUCT;
    d[1] = (uint8_t)strlen(name);
    memcpy(&d[2], name, strlen(name));
    send_cmd(0, CH9329_CMD_SET_USB_STRING, d, 2 + strlen(name), 1);
    CHECK_EQ(reply_status(1), CH9329_STATUS_OK);
    CHECK_EQ(reply_cmd(1), 0x8B);
    CHECK_EQ(R.fake.stores, 1);
    feed_hex("57 AB 00 0A 01 01 0E", 2);
    const fake_event_t *e = fake_nth(&R.fake, FK_REPLY, 2);
    CHECK(e != NULL && e->len == 6 + 2 + strlen(name));
    if (e != NULL && e->len == 6 + 2 + strlen(name)) {
        CHECK_EQ(e->bytes[5], 1);
        CHECK_EQ(e->bytes[6], strlen(name));
        CHECK_MEM(&e->bytes[7], name, strlen(name));
    }
    /* 23 characters is the maximum */
    d[0] = CH9329_STR_SERIAL;
    d[1] = 23;
    memset(&d[2], 'x', 23);
    send_cmd(0, CH9329_CMD_SET_USB_STRING, d, 25, 3);
    CHECK_EQ(reply_status(3), CH9329_STATUS_OK);
    /* invalid: 24 chars, length mismatch, kind 3, control char, too short */
    uint8_t d24[26];
    d24[0] = 0;
    d24[1] = 24;
    memset(&d24[2], 'y', 24);
    send_cmd(0, CH9329_CMD_SET_USB_STRING, d24, 26, 4);
    CHECK_EQ(reply_status(4), CH9329_STATUS_BAD_PARAM);
    send_cmd(0, CH9329_CMD_SET_USB_STRING, (const uint8_t *)"\x00\x03" "ab", 4, 5);
    CHECK_EQ(reply_status(5), CH9329_STATUS_BAD_PARAM);
    send_cmd(0, CH9329_CMD_SET_USB_STRING, (const uint8_t *)"\x03\x01" "a", 3, 6);
    CHECK_EQ(reply_status(6), CH9329_STATUS_BAD_PARAM);
    send_cmd(0, CH9329_CMD_SET_USB_STRING, (const uint8_t *)"\x00\x02" "a\n", 4, 7);
    CHECK_EQ(reply_status(7), CH9329_STATUS_BAD_PARAM);
    send_cmd(0, CH9329_CMD_SET_USB_STRING, (const uint8_t *)"\x00", 1, 8);
    CHECK_EQ(reply_status(8), CH9329_STATUS_BAD_PARAM);
    CHECK_EQ(R.fake.stores, 2);
    /* empty string clears */
    send_cmd(0, CH9329_CMD_SET_USB_STRING, (const uint8_t *)"\x01\x00", 2, 9);
    CHECK_EQ(reply_status(9), CH9329_STATUS_OK);
    CHECK_EQ(R.core.stored.str[1].len, 0);
    CHECK_EQ(R.core.stored.str[1].text[0], 0);
    /* active strings change only at reboot */
    CHECK_EQ(R.core.active.str[CH9329_STR_SERIAL].len, 0);
    reboot();
    CHECK_EQ(R.core.active.str[CH9329_STR_SERIAL].len, 23);
    CHECK(ch9329_strings_valid(&R.core.active));
}

TEST(test_reset)
{
    rig_init();
    feed_hex("57 AB 00 0F 00 11", 0);
    CHECK_REPLY(0, "57 AB 00 8F 01 00 92");
    CHECK_EQ(R.fake.restarts, 1);
    /* restart requested after the reply */
    CHECK(R.fake.n == 2 && R.fake.ev[0].kind == FK_REPLY && R.fake.ev[1].kind == FK_RESTART);
    /* broadcast reset: restart, no reply */
    feed_hex("57 AB FF 0F 00 10", 1);
    CHECK_EQ(replies(), 1);
    CHECK_EQ(R.fake.restarts, 2);
    /* reset with data: E5, no restart */
    feed_hex("57 AB 00 0F 01 00 12", 2);
    CHECK_EQ(reply_status(1), CH9329_STATUS_BAD_PARAM);
    CHECK_EQ(R.fake.restarts, 2);
}

/* ---- robustness -------------------------------------------------------------------------- */

TEST(test_fuzz_random_streams)
{
    rig_init();
    rng_state = 0xC0FFEEu;
    uint8_t buf[97];
    uint32_t now = 0;
    for (int iter = 0; iter < 20000; iter++) {
        size_t n = 1 + rng() % sizeof(buf);
        for (size_t i = 0; i < n; i++) {
            uint32_t r = rng();
            /* bias towards protocol-looking bytes so real parsing paths are exercised */
            switch (r % 8) {
            case 0:
                buf[i] = 0x57;
                break;
            case 1:
                buf[i] = 0xAB;
                break;
            case 2:
                buf[i] = (uint8_t)(r >> 8) % 0x12;
                break;
            default:
                buf[i] = (uint8_t)(r >> 16);
                break;
            }
        }
        feed_bytes(buf, n, now);
        CHECK(R.core.parser.n < CH9329_MAX_FRAME);
        now += rng() % 6;
        ch9329_core_poll(&R.core, now);
        if (R.fake.n > FAKE_MAX - 64) {
            fake_clear(&R.fake);
        }
        R.fake.ready = (rng() & 1) != 0;
    }
    CHECK_EQ(R.fake.bad_replies, 0);
    /* The random data must have exercised every parser path. */
    CHECK(R.core.parser.stats.bad_sum > 0);
    CHECK(R.core.parser.stats.bad_len > 0);
    CHECK(R.core.parser.stats.timeouts > 0);
    /* Still in sync afterwards. */
    ch9329_core_poll(&R.core, now + 1000);
    fake_clear(&R.fake);
    R.fake.ready = true;
    /* Random SET frames can only change flash; the running config (address 0) is unchanged. */
    feed_hex("57 AB FF 02 08 00 00 04 00 00 00 00 00 0F", now + 2000);
    CHECK_EQ(fake_count(&R.fake, FK_KB), 1);
    CHECK_EQ(replies(), 0);
}

TEST(test_parser_reset_and_null_feed)
{
    rig_init();
    feed_hex("57 AB 00 02", 0);
    CHECK_EQ(R.core.parser.n, 4);
    ch9329_parser_reset(&R.core.parser);
    CHECK_EQ(R.core.parser.n, 0);
    ch9329_core_feed(&R.core, NULL, 5, 0);
    ch9329_core_feed(&R.core, (const uint8_t *)"", 0, 0);
    feed_hex(V_GET_INFO, 1);
    CHECK_REPLY(0, R_INFO_READY);
    /* A standalone parser with no callback just counts. */
    ch9329_parser_t p;
    ch9329_parser_init(&p);
    uint8_t f[16];
    size_t n = hex_bytes(V_GET_INFO, f, sizeof(f));
    ch9329_parser_feed(&p, f, n, 0, 3, NULL, NULL);
    CHECK_EQ(p.stats.frames, 1);
}

void run_proto_tests(void);
void run_proto_tests(void)
{
    RUN(test_encoder_matches_wch_vectors);
    RUN(test_get_info);
    RUN(test_keyboard_vectors);
    RUN(test_media_vector);
    RUN(test_media_every_bit_maps_to_same_bit);
    RUN(test_acpi);
    RUN(test_mouse_vectors);
    RUN(test_mouse_clamp_and_button_mask);
    RUN(test_abs_scale);
    RUN(test_abs_mouse_forwarded);
    RUN(test_abs_mouse_out_of_range_e5);
    RUN(test_abs_mouse_link_down_e6);
    RUN(test_abs_mouse_without_pointer);
    RUN(test_get_info_bridge_bytes);
    RUN(test_get_info_report_period);
    RUN(test_rel_run_schedule);
    RUN(test_rel_run_back_to_back_keeps_the_schedule);
    RUN(test_rel_run_late_steps_sent_not_skipped);
    RUN(test_rel_run_stops_at_first_failure);
    RUN(test_rel_run_parameters);
    RUN(test_rel_run_gating);
    RUN(test_kb_reserved_byte_forced_zero);
    RUN(test_merged_stream_one_chunk);
    RUN(test_split_stream_byte_by_byte);
    RUN(test_split_stream_random_chunks);
    RUN(test_header_split_across_chunks);
    RUN(test_garbage_before_header);
    RUN(test_bad_checksum_replies_e4);
    RUN(test_lost_last_byte_recovers_next_frame);
    RUN(test_len_over_64_resyncs_silently);
    RUN(test_max_len_frame_split);
    RUN(test_timeout_replies_e1);
    RUN(test_timeout_short_partial_is_silent);
    RUN(test_gap_detected_on_feed_without_poll);
    RUN(test_slow_bytes_within_interval);
    RUN(test_timeout_recovers_frame_behind_false_header);
    RUN(test_timeout_trailing_partial_discarded_once);
    RUN(test_timeout_wraparound);
    RUN(test_rx_slack_and_interval_from_config);
    RUN(test_char_time);
    RUN(test_address_zero_accepts_any);
    RUN(test_address_filter_nonzero);
    RUN(test_errors_respect_address);
    RUN(test_unknown_commands_e3);
    RUN(test_bad_parameters_e5);
    RUN(test_link_not_ready_e6);
    RUN(test_sink_failure_e6);
    RUN(test_sink_unexpected_status_is_e6);
    RUN(test_per_report_delivery);
    RUN(test_ok_if_and_only_if_delivered);
    RUN(test_broadcast_failure_stays_silent);
    RUN(test_null_sink_callbacks);
    RUN(test_work_mode_keyboard_only);
    RUN(test_work_mode_mouse_only);
    RUN(test_work_mode_custom_acts_composite);
    RUN(test_get_default_config);
    RUN(test_set_get_config_round_trip);
    RUN(test_set_config_validation);
    RUN(test_set_config_store_failure);
    RUN(test_set_default_config);
    RUN(test_invalid_flash_falls_back_to_defaults);
    RUN(test_usb_strings);
    RUN(test_reset);
    RUN(test_fuzz_random_streams);
    RUN(test_parser_reset_and_null_feed);
}
