/*
 * Incremental CH9329 frame parser.
 *
 * Invariant between calls: buf[0..n) is either empty, a lone 0x57, or starts with 57 AB and is
 * a strict prefix of one candidate frame (so n < 6 + LEN <= 70 once LEN is known). Every path
 * that appends a byte re-establishes the invariant before returning, which bounds n by
 * CH9329_MAX_FRAME without any other check.
 *
 * Resynchronisation: when a candidate frame is rejected (LEN > 64, bad checksum, timeout) only
 * its two header bytes are dropped and the rest of the buffer is scanned again, so a real frame
 * hidden behind a false or truncated one is still found (e.g. a frame that lost its last byte
 * swallows the 0x57 of the next frame; the next frame's remaining bytes are then recovered).
 */
#include <string.h>

#include "ch9329_proto.h"

void ch9329_parser_init(ch9329_parser_t *p)
{
    memset(p, 0, sizeof(*p));
}

void ch9329_parser_reset(ch9329_parser_t *p)
{
    p->stats.discarded += p->n;
    p->n = 0;
}

static void drop(ch9329_parser_t *p, size_t count)
{
    if (count >= p->n) {
        p->n = 0;
        return;
    }
    memmove(p->buf, p->buf + count, p->n - count);
    p->n = (uint8_t)(p->n - count);
}

/* Drop leading bytes until the buffer starts with 57 AB, is a lone trailing 57, or is empty. */
static void align(ch9329_parser_t *p)
{
    size_t i = 0;
    while (i < p->n) {
        if (p->buf[i] == CH9329_HEAD0) {
            if (i + 1u >= p->n || p->buf[i + 1u] == CH9329_HEAD1) {
                break;
            }
        }
        i++;
    }
    if (i > 0u) {
        p->stats.discarded += (uint32_t)i;
        drop(p, i);
    }
}

/*
 * Deliver every complete frame at the start of the buffer. With `flush`, a trailing partial
 * frame is discarded silently afterwards (used after a timeout: those bytes are as old as the
 * frame that just expired, so they must not start a new timeout and a second E1).
 */
static void process(ch9329_parser_t *p, bool flush, ch9329_event_cb cb, void *user)
{
    for (;;) {
        align(p);
        if (p->n < 5u) {
            break;
        }
        const uint8_t len = p->buf[4];
        if (len > CH9329_MAX_DATA) {
            /* Impossible length: a false header (or a corrupted LEN). Resync after 57 AB. */
            p->stats.bad_len++;
            p->stats.discarded += 2u;
            drop(p, 2u);
            continue;
        }
        const size_t total = (size_t)len + CH9329_OVERHEAD;
        if (p->n < total) {
            break;
        }
        ch9329_event_t ev = {
            .addr = p->buf[2],
            .cmd = p->buf[3],
            .len = len,
            .data = &p->buf[5],
            .have = (uint8_t)total,
        };
        if (ch9329_checksum(p->buf, total - 1u) == p->buf[total - 1u]) {
            ev.type = CH9329_EV_FRAME;
            p->stats.frames++;
            if (cb != NULL) {
                cb(user, &ev);
            }
            drop(p, total);
        } else {
            ev.type = CH9329_EV_BAD_SUM;
            p->stats.bad_sum++;
            if (cb != NULL) {
                cb(user, &ev);
            }
            p->stats.discarded += 2u;
            drop(p, 2u);
        }
    }
    if (flush && p->n > 0u) {
        p->stats.discarded += p->n;
        p->n = 0;
    }
}

void ch9329_parser_poll(ch9329_parser_t *p, uint32_t now_ms, uint32_t timeout_ms, ch9329_event_cb cb,
                        void *user)
{
    if (p->n == 0u) {
        return;
    }
    /* Unsigned subtraction: correct across the 2^32 ms wrap-around. */
    if ((uint32_t)(now_ms - p->last_rx_ms) < timeout_ms) {
        return;
    }
    ch9329_event_t ev = {
        .type = CH9329_EV_TIMEOUT,
        .addr = p->n >= 3u ? p->buf[2] : 0u,
        .cmd = p->n >= 4u ? p->buf[3] : 0u,
        .len = 0,
        .data = NULL,
        .have = p->n,
    };
    p->stats.timeouts++;
    if (cb != NULL) {
        cb(user, &ev);
    }
    /* Drop the expired candidate's header and look for complete frames behind it. */
    const size_t hdr = p->n >= 2u ? 2u : p->n;
    p->stats.discarded += (uint32_t)hdr;
    drop(p, hdr);
    process(p, true, cb, user);
}

void ch9329_parser_feed(ch9329_parser_t *p, const uint8_t *data, size_t len, uint32_t now_ms,
                        uint32_t timeout_ms, ch9329_event_cb cb, void *user)
{
    if (data == NULL || len == 0u) {
        return;
    }
    /* A gap longer than the timeout ends the pending frame before new bytes are considered. */
    ch9329_parser_poll(p, now_ms, timeout_ms, cb, user);
    for (size_t i = 0; i < len; i++) {
        const uint8_t b = data[i];
        if (p->n == 0u && b != CH9329_HEAD0) {
            p->stats.discarded++; /* fast path for garbage between frames */
            continue;
        }
        if (p->n >= sizeof(p->buf)) {
            /* Unreachable while the invariant holds; kept so a bug can never overflow buf. */
            ch9329_parser_reset(p);
            if (b != CH9329_HEAD0) {
                p->stats.discarded++;
                continue;
            }
        }
        p->buf[p->n++] = b;
        process(p, false, cb, user);
    }
    p->last_rx_ms = now_ms;
}

uint32_t ch9329_parser_ms_until_timeout(const ch9329_parser_t *p, uint32_t now_ms, uint32_t timeout_ms)
{
    if (p->n == 0u) {
        return UINT32_MAX;
    }
    const uint32_t elapsed = (uint32_t)(now_ms - p->last_rx_ms);
    return elapsed >= timeout_ms ? 0u : timeout_ms - elapsed;
}
