/*
 * Host tests for hid_confirm (USB report completion matching): the scripted races from the
 * review (a late completion of an unconfirmed report must never confirm the next one), link
 * resets, counter wrap-around, and a randomised model of a TinyUSB interrupt IN endpoint.
 */
#include <stdbool.h>
#include <stdlib.h>

#include "hid_confirm.h"
#include "tinytest.h"

TEST(test_confirm_in_order)
{
    hid_confirm_t t;
    hid_confirm_init(&t);
    for (int i = 0; i < 5; i++) {
        const hid_confirm_ticket_t k = hid_confirm_reserve(&t);
        CHECK_EQ(hid_confirm_state(&t, k), HID_CONFIRM_WAITING);
        hid_confirm_completed(&t);
        CHECK_EQ(hid_confirm_state(&t, k), HID_CONFIRM_READ);
    }
}

TEST(test_confirm_late_completion_not_credited_to_next)
{
    /* Report 1 is queued, the host does not read it in time: the bridge answers E6 and moves on.
     * The endpoint frees up (TinyUSB clears "busy" first), report 2 is queued, and only then
     * does the completion callback of report 1 run. It must not confirm report 2. */
    hid_confirm_t t;
    hid_confirm_init(&t);
    const hid_confirm_ticket_t r1 = hid_confirm_reserve(&t);
    CHECK_EQ(hid_confirm_state(&t, r1), HID_CONFIRM_WAITING); /* timed out: E6 */
    const hid_confirm_ticket_t r2 = hid_confirm_reserve(&t);
    hid_confirm_completed(&t); /* report 1's late completion */
    CHECK_EQ(hid_confirm_state(&t, r2), HID_CONFIRM_WAITING);
    CHECK_EQ(hid_confirm_state(&t, r1), HID_CONFIRM_READ); /* it was read, just late */
    hid_confirm_completed(&t); /* report 2's own */
    CHECK_EQ(hid_confirm_state(&t, r2), HID_CONFIRM_READ);
}

TEST(test_confirm_completion_before_queue_returns)
{
    /* The completion can run on the other core before the queueing call has returned: the
     * ticket is reserved first, so it still counts. */
    hid_confirm_t t;
    hid_confirm_init(&t);
    const hid_confirm_ticket_t r1 = hid_confirm_reserve(&t);
    hid_confirm_completed(&t);
    CHECK_EQ(hid_confirm_state(&t, r1), HID_CONFIRM_READ);
    /* ...and with a stale completion of an older report arriving in the same window. */
    const hid_confirm_ticket_t r2 = hid_confirm_reserve(&t); /* E6, never confirmed */
    (void)r2;
    const hid_confirm_ticket_t r3 = hid_confirm_reserve(&t);
    hid_confirm_completed(&t); /* r2's late one */
    CHECK_EQ(hid_confirm_state(&t, r3), HID_CONFIRM_WAITING);
    hid_confirm_completed(&t); /* r3's own */
    CHECK_EQ(hid_confirm_state(&t, r3), HID_CONFIRM_READ);
}

TEST(test_confirm_cancel)
{
    /* tud_hid_n_report() refused the report: its ticket is withdrawn, nothing waits for it. */
    hid_confirm_t t;
    hid_confirm_init(&t);
    const hid_confirm_ticket_t r1 = hid_confirm_reserve(&t);
    hid_confirm_cancel(&t, r1);
    const hid_confirm_ticket_t r2 = hid_confirm_reserve(&t);
    hid_confirm_completed(&t);
    CHECK_EQ(hid_confirm_state(&t, r2), HID_CONFIRM_READ);
    /* A stale completion arriving between reserve and cancel belongs to the older report. */
    const hid_confirm_ticket_t r3 = hid_confirm_reserve(&t); /* E6 */
    const hid_confirm_ticket_t r4 = hid_confirm_reserve(&t);
    hid_confirm_completed(&t); /* r3's */
    hid_confirm_cancel(&t, r4);
    CHECK_EQ(hid_confirm_state(&t, r3), HID_CONFIRM_READ);
    const hid_confirm_ticket_t r5 = hid_confirm_reserve(&t);
    CHECK_EQ(hid_confirm_state(&t, r5), HID_CONFIRM_WAITING);
    hid_confirm_completed(&t);
    CHECK_EQ(hid_confirm_state(&t, r5), HID_CONFIRM_READ);
}

TEST(test_confirm_reset)
{
    /* Bus reset / new configuration while a report is in flight: that report is lost, the next
     * one is confirmed by its own completion, not held back by the missing one. */
    hid_confirm_t t;
    hid_confirm_init(&t);
    const hid_confirm_ticket_t r1 = hid_confirm_reserve(&t);
    hid_confirm_reset(&t);
    CHECK_EQ(hid_confirm_state(&t, r1), HID_CONFIRM_LOST);
    const hid_confirm_ticket_t r2 = hid_confirm_reserve(&t);
    CHECK_EQ(hid_confirm_state(&t, r2), HID_CONFIRM_WAITING);
    hid_confirm_completed(&t);
    CHECK_EQ(hid_confirm_state(&t, r2), HID_CONFIRM_READ);
    /* A reset between reserve and cancel: nothing to undo. */
    const hid_confirm_ticket_t r3 = hid_confirm_reserve(&t);
    hid_confirm_reset(&t);
    hid_confirm_cancel(&t, r3);
    const hid_confirm_ticket_t r4 = hid_confirm_reserve(&t);
    hid_confirm_completed(&t);
    CHECK_EQ(hid_confirm_state(&t, r4), HID_CONFIRM_READ);
    /* A completion with nothing outstanding (a transfer that slipped past a reset) is ignored,
     * not banked for the next report. */
    hid_confirm_completed(&t);
    const hid_confirm_ticket_t r5 = hid_confirm_reserve(&t);
    CHECK_EQ(hid_confirm_state(&t, r5), HID_CONFIRM_WAITING);
}

TEST(test_confirm_wraps)
{
    hid_confirm_t t = {.gen = 0xFFFFFFFFu, .queued = 0xFFFFFFFEu, .done = 0xFFFFFFFEu};
    const hid_confirm_ticket_t a = hid_confirm_reserve(&t); /* seq 0xFFFFFFFF */
    const hid_confirm_ticket_t b = hid_confirm_reserve(&t); /* seq 0 */
    CHECK_EQ(hid_confirm_state(&t, a), HID_CONFIRM_WAITING);
    hid_confirm_completed(&t);
    CHECK_EQ(hid_confirm_state(&t, a), HID_CONFIRM_READ);
    CHECK_EQ(hid_confirm_state(&t, b), HID_CONFIRM_WAITING);
    hid_confirm_completed(&t);
    CHECK_EQ(hid_confirm_state(&t, b), HID_CONFIRM_READ);
    hid_confirm_reset(&t); /* generation wraps to 0 */
    CHECK_EQ(t.gen, 0);
    CHECK_EQ(hid_confirm_state(&t, b), HID_CONFIRM_LOST);
}

/*
 * Randomised model of the firmware + TinyUSB on one endpoint. The endpoint holds at most one
 * transfer; when the host reads it, "busy" clears at once but the completion callback runs
 * later (any number of steps). The bridge queues a report only on a free endpoint, may give up
 * waiting (E6) at any time, and TinyUSB may refuse a report. A link reset drops the transfer in
 * flight without a completion (events queued before it are delivered first, as in tud_task).
 * Properties: READ only after that very report was read by the host; LOST only after a reset;
 * a report read by the host whose completion ran, with no reset since, is READ (no drift).
 */
typedef struct {
    bool in_flight;       /* a transfer is armed and not yet read */
    uint32_t flight_id;   /* report id in the endpoint */
    int callbacks;        /* completion callbacks pending (endpoint already free), oldest first */
    uint32_t pending_ids[64];
} ep_model_t;

static uint32_t rnd(uint32_t *s)
{
    *s = *s * 1103515245u + 12345u;
    return (*s >> 16) & 0x7FFFu;
}

TEST(test_confirm_random_model)
{
    enum { MAX_REPORTS = 20000 };
    static bool read_by_host[MAX_REPORTS + 1];
    static bool callback_ran[MAX_REPORTS + 1];
    static uint32_t gen_of[MAX_REPORTS + 1];
    memset(read_by_host, 0, sizeof(read_by_host));
    memset(callback_ran, 0, sizeof(callback_ran));
    uint32_t seed = 0xC0FFEEu;
    hid_confirm_t t;
    hid_confirm_init(&t);
    ep_model_t ep = {0};
    uint32_t next_id = 1;
    uint32_t confirmed = 0, lost = 0, gave_up = 0, resets = 0;
    uint32_t reset_gen = 0; /* model's own count of resets */

    while (next_id <= MAX_REPORTS) {
        /* The bridge: queue a report once the endpoint is free. */
        if (!ep.in_flight) {
            const uint32_t id = next_id++;
            const hid_confirm_ticket_t k = hid_confirm_reserve(&t);
            if (rnd(&seed) % 20u == 0u) {
                hid_confirm_cancel(&t, k); /* tud_hid_n_report() said no */
                continue;
            }
            ep.in_flight = true;
            ep.flight_id = id;
            gen_of[id] = reset_gen;
            /* Wait for it, with the USB side making progress in between. */
            const uint32_t patience = rnd(&seed) % 6u;
            for (uint32_t step = 0;; step++) {
                const uint32_t r = rnd(&seed) % 100u;
                if (r < 30u && ep.in_flight) { /* the host reads the endpoint */
                    ep.in_flight = false;
                    read_by_host[ep.flight_id] = true;
                    CHECK(ep.callbacks < 64);
                    ep.pending_ids[ep.callbacks++] = ep.flight_id;
                } else if (r < 65u && ep.callbacks > 0) { /* a completion callback runs */
                    const uint32_t done_id = ep.pending_ids[0];
                    for (int i = 1; i < ep.callbacks; i++) {
                        ep.pending_ids[i - 1] = ep.pending_ids[i];
                    }
                    ep.callbacks--;
                    callback_ran[done_id] = true;
                    hid_confirm_completed(&t);
                } else if (r < 67u) { /* link reset: pending callbacks first, then the reset */
                    while (ep.callbacks > 0) {
                        callback_ran[ep.pending_ids[--ep.callbacks]] = true;
                        hid_confirm_completed(&t);
                    }
                    ep.in_flight = false; /* dropped, never read */
                    hid_confirm_reset(&t);
                    reset_gen++;
                    resets++;
                }
                const hid_confirm_state_t st = hid_confirm_state(&t, k);
                if (st == HID_CONFIRM_READ) {
                    CHECK(read_by_host[id]);
                    CHECK(callback_ran[id]);
                    confirmed++;
                    break;
                }
                if (st == HID_CONFIRM_LOST) {
                    CHECK(reset_gen != gen_of[id]);
                    lost++;
                    break;
                }
                /* Still waiting although its own completion ran in this generation: drift. */
                CHECK(!(callback_ran[id] && reset_gen == gen_of[id]));
                if (step >= patience) {
                    gave_up++; /* E6: the report stays in the endpoint, the bridge moves on */
                    break;
                }
            }
        } else {
            /* Endpoint still busy with an unconfirmed report: the host may read it later. */
            if (rnd(&seed) % 2u == 0u) {
                ep.in_flight = false;
                read_by_host[ep.flight_id] = true;
                CHECK(ep.callbacks < 64);
                ep.pending_ids[ep.callbacks++] = ep.flight_id;
            } else if (rnd(&seed) % 50u == 0u) {
                while (ep.callbacks > 0) {
                    callback_ran[ep.pending_ids[--ep.callbacks]] = true;
                    hid_confirm_completed(&t);
                }
                ep.in_flight = false;
                hid_confirm_reset(&t);
                reset_gen++;
                resets++;
            }
        }
    }
    /* Every path was exercised. */
    CHECK(confirmed > 1000u);
    CHECK(gave_up > 1000u);
    CHECK(lost > 10u);
    CHECK(resets > 10u);
}

void run_hid_confirm_tests(void);
void run_hid_confirm_tests(void)
{
    RUN(test_confirm_in_order);
    RUN(test_confirm_late_completion_not_credited_to_next);
    RUN(test_confirm_completion_before_queue_returns);
    RUN(test_confirm_cancel);
    RUN(test_confirm_reset);
    RUN(test_confirm_wraps);
    RUN(test_confirm_random_model);
}
