/* USB report completion matching (see hid_confirm.h). */
#include "hid_confirm.h"

void hid_confirm_init(hid_confirm_t *t)
{
    t->gen = 0;
    t->queued = 0;
    t->done = 0;
}

hid_confirm_ticket_t hid_confirm_reserve(hid_confirm_t *t)
{
    t->queued++;
    const hid_confirm_ticket_t ticket = {.gen = t->gen, .seq = t->queued};
    return ticket;
}

void hid_confirm_cancel(hid_confirm_t *t, hid_confirm_ticket_t ticket)
{
    /* Only the newest ticket can be cancelled (it was never queued, so nothing completes for it).
     * After a reset the counts were already levelled: nothing to undo. */
    if (ticket.gen == t->gen && ticket.seq == t->queued && t->done != t->queued) {
        t->queued--;
    }
}

void hid_confirm_completed(hid_confirm_t *t)
{
    if (t->done != t->queued) {
        t->done++; /* the oldest outstanding report: completions come in queue order */
    }
}

void hid_confirm_reset(hid_confirm_t *t)
{
    t->gen++;
    t->done = t->queued;
}

hid_confirm_state_t hid_confirm_state(const hid_confirm_t *t, hid_confirm_ticket_t ticket)
{
    if (ticket.gen != t->gen) {
        return HID_CONFIRM_LOST;
    }
    /* done has reached seq: this report's own completion arrived (wrap-safe). */
    return (int32_t)(t->done - ticket.seq) >= 0 ? HID_CONFIRM_READ : HID_CONFIRM_WAITING;
}
