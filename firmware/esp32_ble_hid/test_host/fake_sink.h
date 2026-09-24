/* Recording ch9329_sink_t for the host tests: stands in for the BLE HID layer, NVS and the UART. */
#ifndef FAKE_SINK_H
#define FAKE_SINK_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "ch9329_proto.h"

#define FAKE_MAX 256

typedef enum { FK_REPLY, FK_KB, FK_MOUSE, FK_CONSUMER, FK_SYSTEM, FK_ABS, FK_STORE, FK_RESTART } fake_kind_t;

typedef struct {
    fake_kind_t kind;
    uint8_t bytes[CH9329_MAX_FRAME];
    size_t len;
    uint32_t t; /* fake clock when recorded */
} fake_event_t;

typedef struct {
    fake_event_t ev[FAKE_MAX];
    size_t n;          /* events recorded (capped at FAKE_MAX; `overflow` counts the rest) */
    size_t overflow;
    size_t bad_replies; /* replies that failed the independent frame check */

    /* Delivery model, mirroring the firmware's BLE layer: a report callback returns OK and
     * records the report only if the link is up (`ready`), this report type is deliverable
     * (bit `kind` of `reject_kinds` clear) and the scripted status is OK. Otherwise nothing is
     * recorded (the report never left the bridge) and the callback returns that status. */
    bool ready;
    unsigned reject_kinds; /* bit (1u << FK_MOUSE) etc.: report type not subscribed */
    uint8_t leds;
    uint8_t hid_status;    /* returned by every report callback while ready (OK by default) */
    uint8_t script[64];    /* if script_n > 0: per-call statuses consumed before hid_status */
    size_t script_n, script_pos;
    size_t attempts;       /* report callbacks invoked */
    size_t rejected;       /* ... that did not return OK */

    bool flash_valid;   /* persist_load succeeds */
    bool store_ok;      /* persist_store succeeds */
    ch9329_persist_t flash;
    int stores;
    int restarts;

    /* Fake clock for SEND_MS_REL_RUN: sleep_until_ms jumps to the target (never backwards) and
     * every report callback advances it by `report_cost_ms` (time the link takes to accept). */
    uint32_t clock;
    uint32_t report_cost_ms;
    size_t sleeps;
} fake_t;

void fake_init(fake_t *f);
/* Every callback set, including abs_mouse_report and the REL_RUN clock. */
ch9329_sink_t fake_sink(fake_t *f);
void fake_clear(fake_t *f); /* forget recorded events and counters, keep flash and settings */
/* The next n report callbacks return these statuses (then hid_status again). */
void fake_script(fake_t *f, const uint8_t *statuses, size_t n);

size_t fake_count(const fake_t *f, fake_kind_t kind);
/* i-th event of a kind, or NULL. */
const fake_event_t *fake_nth(const fake_t *f, fake_kind_t kind, size_t i);

/* Parse "57 AB 00 01 00 03" into out; returns the byte count. Aborts on malformed input. */
size_t hex_bytes(const char *s, uint8_t *out, size_t cap);

/* Independent frame check (does not use ch9329_checksum): header, LEN, checksum, reply flag. */
bool frame_ok(const uint8_t *f, size_t len);

#endif /* FAKE_SINK_H */
