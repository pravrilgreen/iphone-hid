#include "fake_sink.h"

#include "tinytest.h"

#include <ctype.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static void record(fake_t *f, fake_kind_t kind, const uint8_t *bytes, size_t len)
{
    if (f->n >= FAKE_MAX) {
        f->overflow++;
        return;
    }
    if (len > sizeof(f->ev[0].bytes)) {
        fprintf(stderr, "fake_sink: event of %zu bytes is too long\n", len);
        abort();
    }
    fake_event_t *e = &f->ev[f->n++];
    e->kind = kind;
    e->len = len;
    e->t = f->clock;
    if (len > 0) {
        memcpy(e->bytes, bytes, len);
    }
}

bool frame_ok(const uint8_t *f, size_t len)
{
    if (len < 6 || len > 70 || f[0] != 0x57 || f[1] != 0xAB) {
        return false;
    }
    if ((size_t)f[4] + 6 != len) {
        return false;
    }
    unsigned sum = 0;
    for (size_t i = 0; i + 1 < len; i++) {
        sum += f[i];
    }
    if ((sum & 0xFF) != f[len - 1]) {
        return false;
    }
    /* Every bridge frame is a reply: bit 7 set; error replies carry exactly one status byte. */
    if (!(f[3] & 0x80)) {
        return false;
    }
    if ((f[3] & 0xC0) == 0xC0 && f[4] != 1) {
        return false;
    }
    return true;
}

static void cb_reply(void *ctx, const uint8_t *frame, size_t len)
{
    fake_t *f = ctx;
    /* Every reply of every test goes through the independent check. */
    if (!frame_ok(frame, len)) {
        f->bad_replies++;
        tt_fail(__FILE__, __LINE__, "reply frame failed the independent header/LEN/checksum check");
    }
    record(f, FK_REPLY, frame, len);
}

/* One report callback for every kind: see the delivery model in fake_sink.h. */
static uint8_t deliver(fake_t *f, fake_kind_t kind, const uint8_t *report, size_t len)
{
    f->attempts++;
    f->clock += f->report_cost_us;
    if (f->slow_at != 0 && f->attempts == f->slow_at) {
        f->clock += f->slow_cost_us;
    }
    f->accepted_at = f->clock;
    uint8_t status = f->hid_status;
    if (f->script_pos < f->script_n) {
        status = f->script[f->script_pos++];
    }
    if (!f->ready || (f->reject_kinds & (1u << kind)) != 0) {
        status = CH9329_STATUS_EXEC_FAILED;
    }
    if (status != CH9329_STATUS_OK) {
        f->rejected++;
        return status;
    }
    record(f, kind, report, len);
    f->clock += f->confirm_cost_us;
    return CH9329_STATUS_OK;
}

static uint8_t cb_kb(void *ctx, const uint8_t report[CH9329_KB_REPORT_LEN])
{
    return deliver(ctx, FK_KB, report, CH9329_KB_REPORT_LEN);
}

static uint8_t cb_mouse(void *ctx, const uint8_t report[CH9329_MOUSE_REPORT_LEN])
{
    return deliver(ctx, FK_MOUSE, report, CH9329_MOUSE_REPORT_LEN);
}

static uint8_t cb_consumer(void *ctx, const uint8_t report[CH9329_CONSUMER_REPORT_LEN])
{
    return deliver(ctx, FK_CONSUMER, report, CH9329_CONSUMER_REPORT_LEN);
}

static uint8_t cb_system(void *ctx, const uint8_t report[CH9329_SYSTEM_REPORT_LEN])
{
    return deliver(ctx, FK_SYSTEM, report, CH9329_SYSTEM_REPORT_LEN);
}

static uint8_t cb_abs(void *ctx, const uint8_t report[CH9329_ABS_REPORT_LEN])
{
    return deliver(ctx, FK_ABS, report, CH9329_ABS_REPORT_LEN);
}

static uint32_t cb_clock(void *ctx)
{
    return ((fake_t *)ctx)->clock;
}

static void cb_sleep_until(void *ctx, uint32_t t_us)
{
    fake_t *f = ctx;
    f->sleeps++;
    if ((int32_t)(t_us - f->clock) > 0) {
        f->clock = t_us;
    }
}

static uint32_t cb_accepted(void *ctx)
{
    return ((fake_t *)ctx)->accepted_at;
}

static bool cb_ready(void *ctx)
{
    return ((fake_t *)ctx)->ready;
}

static uint8_t cb_leds(void *ctx)
{
    return ((fake_t *)ctx)->leds;
}

static uint16_t cb_report_period(void *ctx)
{
    return ((fake_t *)ctx)->report_period;
}

static bool cb_load(void *ctx, ch9329_persist_t *out)
{
    fake_t *f = ctx;
    if (!f->flash_valid) {
        return false;
    }
    *out = f->flash;
    return true;
}

static bool cb_store(void *ctx, const ch9329_persist_t *p)
{
    fake_t *f = ctx;
    record(f, FK_STORE, NULL, 0);
    if (!f->store_ok) {
        return false;
    }
    f->flash = *p;
    f->flash_valid = true;
    f->stores++;
    return true;
}

static void cb_restart(void *ctx)
{
    fake_t *f = ctx;
    f->restarts++;
    record(f, FK_RESTART, NULL, 0);
}

void fake_init(fake_t *f)
{
    memset(f, 0, sizeof(*f));
    f->ready = true;
    f->hid_status = CH9329_STATUS_OK;
    f->store_ok = true;
}

void fake_clear(fake_t *f)
{
    f->n = 0;
    f->overflow = 0;
    f->attempts = 0;
    f->rejected = 0;
    f->sleeps = 0;
}

void fake_script(fake_t *f, const uint8_t *statuses, size_t n)
{
    if (n > sizeof(f->script)) {
        fprintf(stderr, "fake_script: %zu statuses, max %zu\n", n, sizeof(f->script));
        abort();
    }
    memcpy(f->script, statuses, n);
    f->script_n = n;
    f->script_pos = 0;
}

ch9329_sink_t fake_sink(fake_t *f)
{
    ch9329_sink_t s = {
        .ctx = f,
        .send_reply = cb_reply,
        .keyboard_report = cb_kb,
        .mouse_report = cb_mouse,
        .consumer_report = cb_consumer,
        .system_report = cb_system,
        .abs_mouse_report = cb_abs,
        .link_ready = cb_ready,
        .leds = cb_leds,
        .report_period = cb_report_period,
        .persist_load = cb_load,
        .persist_store = cb_store,
        .request_restart = cb_restart,
        .clock_us = cb_clock,
        .sleep_until_us = cb_sleep_until,
        .accepted_us = cb_accepted,
    };
    return s;
}

size_t fake_count(const fake_t *f, fake_kind_t kind)
{
    size_t c = 0;
    for (size_t i = 0; i < f->n; i++) {
        if (f->ev[i].kind == kind) {
            c++;
        }
    }
    return c;
}

const fake_event_t *fake_nth(const fake_t *f, fake_kind_t kind, size_t i)
{
    for (size_t k = 0; k < f->n; k++) {
        if (f->ev[k].kind == kind) {
            if (i == 0) {
                return &f->ev[k];
            }
            i--;
        }
    }
    return NULL;
}

size_t hex_bytes(const char *s, uint8_t *out, size_t cap)
{
    size_t n = 0;
    while (*s) {
        if (isspace((unsigned char)*s)) {
            s++;
            continue;
        }
        if (!isxdigit((unsigned char)s[0]) || !isxdigit((unsigned char)s[1]) || n >= cap) {
            fprintf(stderr, "hex_bytes: bad input near \"%s\"\n", s);
            abort();
        }
        char pair[3] = {s[0], s[1], 0};
        out[n++] = (uint8_t)strtoul(pair, NULL, 16);
        s += 2;
    }
    return n;
}
