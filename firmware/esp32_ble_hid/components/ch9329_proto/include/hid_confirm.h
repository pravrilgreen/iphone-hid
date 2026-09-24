/*
 * Matching USB IN-report completions to the report they belong to. Portable, no locking: the
 * caller serialises every call for one endpoint (the firmware holds a spinlock shared with the
 * TinyUSB task).
 *
 * The problem: TinyUSB reports "the host read the report in this endpoint" through
 * tud_hid_report_complete_cb(), which carries no transfer identity (its buffer pointer is the
 * endpoint's buffer, possibly holding the next report already), and it marks the endpoint free
 * BEFORE that callback runs. A report that was not confirmed in time (answered E6) can
 * complete later, while the next report is already queued; counting that late completion for
 * the new report would answer 00 before the phone had read it.
 *
 * The fix: an interrupt IN endpoint carries one transfer at a time and completes them in order,
 * so the n-th completion belongs to the n-th queued report. Counting both sides gives every
 * report a ticket (its sequence number) that only its own completion can satisfy:
 *
 *   ticket = hid_confirm_reserve()      before queueing (the completion may arrive before the
 *   queue the report                    queueing call returns)
 *   if it was not queued: hid_confirm_cancel(ticket)
 *   wait until hid_confirm_state(ticket) != HID_CONFIRM_WAITING, or give up (E6)
 *
 *   completion callback:  hid_confirm_completed()
 *   link reset (the USB configuration was set again or cleared: TinyUSB then drops a pending
 *   transfer without a completion):  hid_confirm_reset()
 *
 * A reset starts a new generation: tickets of the previous one are LOST (never read), and the
 * counts are brought level so later reports are not held back by a completion that will never
 * come. A completion while nothing is outstanding is ignored rather than credited to a future
 * report.
 */
#ifndef HID_CONFIRM_H
#define HID_CONFIRM_H

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef struct {
    uint32_t gen;    /* link generation, bumped by hid_confirm_reset() */
    uint32_t queued; /* tickets handed out (wraps) */
    uint32_t done;   /* completions credited (wraps; never ahead of `queued`) */
} hid_confirm_t;

typedef struct {
    uint32_t gen;
    uint32_t seq;
} hid_confirm_ticket_t;

typedef enum {
    HID_CONFIRM_WAITING = 0, /* not read yet */
    HID_CONFIRM_READ,        /* the host read this very report */
    HID_CONFIRM_LOST,        /* the link was reset since it was queued: it will never be read */
} hid_confirm_state_t;

void hid_confirm_init(hid_confirm_t *t);
hid_confirm_ticket_t hid_confirm_reserve(hid_confirm_t *t);
void hid_confirm_cancel(hid_confirm_t *t, hid_confirm_ticket_t ticket);
void hid_confirm_completed(hid_confirm_t *t);
void hid_confirm_reset(hid_confirm_t *t);
hid_confirm_state_t hid_confirm_state(const hid_confirm_t *t, hid_confirm_ticket_t ticket);

#ifdef __cplusplus
}
#endif

#endif /* HID_CONFIRM_H */
