/*
 * sim_bridge: the portable CH9329 core behind a Linux pseudo-terminal.
 *
 *     ./build/sim_bridge EVENTS_FILE [--not-ready]
 *
 * Prints the pty path (e.g. /dev/pts/5) on stdout, then serves the protocol until stdin closes.
 * The host software opens that path exactly like the bridge's /dev/ttyUSB*. The baud rate the
 * host sets on the pty is ignored.
 *
 * Every HID report the simulated BLE link ACCEPTED, every config write and every restart is
 * appended to EVENTS_FILE as one text line. A report the link refused is not logged (it never
 * reached the phone), exactly like the firmware:
 *
 *     KB 00 00 04 00 00 00 00 00
 *     MOUSE 01 00 00 00
 *     CONSUMER 04 00 00
 *     SYSTEM 01
 *     ABS 00 20 03 40 06 00        (buttons, X lo/hi, Y lo/hi on 0..32767, wheel)
 *     STORE
 *     RESTART work_mode=01 address=00
 *
 * Control lines on stdin (each answered with "ok <line>" on stdout once applied), used by
 * e2e_host_driver.py to drive the simulated BLE side:
 *
 *     ready 0|1          link down / up (GET_INFO byte 1; every report refused with E6 while down)
 *     reject KIND|none   KIND = kb, mouse, consumer, system, abs: that report type is not subscribed
 *     fail N             the next N reports are refused (BLE buffers stayed full), then normal
 *     failat K           the K-th next report is refused (K >= 1), the others go through
 *     delay MS           every report takes MS ms before it is accepted (waiting for BLE buffers)
 *     leds N             keyboard LED byte reported by GET_INFO
 *
 * RESET is simulated as a reboot of the core: the stored configuration becomes active, as on
 * the ESP32. SEND_MS_REL_RUN (0x30) runs on the real monotonic clock. GET_INFO reports output
 * 0x7F (simulator) and the collections of the running work mode with both pointers.
 *
 * Timing: bytes are fed with the time they were read, minus the time the simulated sink spent
 * blocked ("delay"). The firmware gets the same effect from its separate UART reader task:
 * arrival time, not processing time, decides whether a partial frame timed out.
 */
#define _XOPEN_SOURCE 700
#include <errno.h>
#include <fcntl.h>
#include <poll.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <termios.h>
#include <time.h>
#include <unistd.h>

#include "ch9329_proto.h"
#include "hid_report_map.h"

enum { K_KB = 0, K_MOUSE, K_CONSUMER, K_SYSTEM, K_ABS, K_COUNT };
static const char *const KIND_NAMES[K_COUNT] = {"kb", "mouse", "consumer", "system", "abs"};
static const char *const KIND_TAGS[K_COUNT] = {"KB", "MOUSE", "CONSUMER", "SYSTEM", "ABS"};

typedef struct {
    int master;
    FILE *events;
    bool ready;
    unsigned reject; /* bit per K_* */
    unsigned fail_next;
    unsigned fail_skip; /* reports to let through before fail_next applies */
    unsigned delay_ms;
    uint8_t leds;
    bool have_flash;
    ch9329_persist_t flash;
    bool restart;
    uint64_t blocked_ms; /* total time spent inside simulated sink delays */
} sim_t;

static uint64_t mono_ms(void)
{
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (uint64_t)ts.tv_sec * 1000u + (uint64_t)ts.tv_nsec / 1000000u;
}

/* The core's clock: monotonic time minus time spent blocked in the sink (see the header). */
static uint32_t core_now(const sim_t *s)
{
    return (uint32_t)(mono_ms() - s->blocked_ms);
}

static void sleep_ms(unsigned ms)
{
    struct timespec ts = {.tv_sec = (time_t)(ms / 1000u), .tv_nsec = (long)(ms % 1000u) * 1000000L};
    while (nanosleep(&ts, &ts) != 0 && errno == EINTR) {
    }
}

static void log_bytes(sim_t *s, const char *tag, const uint8_t *b, size_t n)
{
    fprintf(s->events, "%s", tag);
    for (size_t i = 0; i < n; i++) {
        fprintf(s->events, " %02X", b[i]);
    }
    fprintf(s->events, "\n");
    fflush(s->events);
}

static void write_all(int fd, const uint8_t *b, size_t n)
{
    while (n > 0) {
        ssize_t w = write(fd, b, n);
        if (w < 0) {
            if (errno == EINTR || errno == EAGAIN) {
                continue;
            }
            perror("sim_bridge: write");
            return;
        }
        b += w;
        n -= (size_t)w;
    }
}

static void cb_reply(void *ctx, const uint8_t *frame, size_t len)
{
    sim_t *s = ctx;
    write_all(s->master, frame, len);
}

/* The simulated BLE link: same decision order as main/ble_hid.c (link, subscription, buffers). */
static uint8_t deliver(sim_t *s, int kind, const uint8_t *r, size_t n)
{
    if (s->delay_ms > 0u) {
        const uint64_t t0 = mono_ms();
        sleep_ms(s->delay_ms);
        s->blocked_ms += mono_ms() - t0;
    }
    if (!s->ready || (s->reject & (1u << kind)) != 0u) {
        return CH9329_STATUS_EXEC_FAILED;
    }
    if (s->fail_skip > 0u) {
        s->fail_skip--;
    } else if (s->fail_next > 0u) {
        s->fail_next--;
        return CH9329_STATUS_EXEC_FAILED;
    }
    log_bytes(s, KIND_TAGS[kind], r, n);
    return CH9329_STATUS_OK;
}

static uint8_t cb_kb(void *ctx, const uint8_t r[CH9329_KB_REPORT_LEN])
{
    return deliver(ctx, K_KB, r, CH9329_KB_REPORT_LEN);
}

static uint8_t cb_mouse(void *ctx, const uint8_t r[CH9329_MOUSE_REPORT_LEN])
{
    return deliver(ctx, K_MOUSE, r, CH9329_MOUSE_REPORT_LEN);
}

static uint8_t cb_consumer(void *ctx, const uint8_t r[CH9329_CONSUMER_REPORT_LEN])
{
    return deliver(ctx, K_CONSUMER, r, CH9329_CONSUMER_REPORT_LEN);
}

static uint8_t cb_system(void *ctx, const uint8_t r[CH9329_SYSTEM_REPORT_LEN])
{
    return deliver(ctx, K_SYSTEM, r, CH9329_SYSTEM_REPORT_LEN);
}

static uint8_t cb_abs(void *ctx, const uint8_t r[CH9329_ABS_REPORT_LEN])
{
    return deliver(ctx, K_ABS, r, CH9329_ABS_REPORT_LEN);
}

static uint32_t cb_clock(void *ctx)
{
    (void)ctx;
    return (uint32_t)mono_ms();
}

/* SEND_MS_REL_RUN pacing: a real sleep, counted as blocked time like a slow BLE buffer. */
static void cb_sleep_until(void *ctx, uint32_t t_ms)
{
    sim_t *s = ctx;
    const uint64_t t0 = mono_ms();
    const int32_t wait = (int32_t)(t_ms - (uint32_t)t0);
    if (wait > 0) {
        sleep_ms((unsigned)wait);
        s->blocked_ms += mono_ms() - t0;
    }
}

/* GET_INFO byte 1: every report type of the composite profile is deliverable. */
static bool cb_ready(void *ctx)
{
    const sim_t *s = ctx;
    return s->ready && s->reject == 0u;
}

static uint8_t cb_leds(void *ctx)
{
    return ((sim_t *)ctx)->leds;
}

static bool cb_load(void *ctx, ch9329_persist_t *out)
{
    sim_t *s = ctx;
    if (!s->have_flash) {
        return false;
    }
    *out = s->flash;
    return true;
}

static bool cb_store(void *ctx, const ch9329_persist_t *p)
{
    sim_t *s = ctx;
    s->flash = *p;
    s->have_flash = true;
    log_bytes(s, "STORE", NULL, 0);
    return true;
}

static void cb_restart(void *ctx)
{
    ((sim_t *)ctx)->restart = true;
}

/* Apply one control line; returns false for a malformed one. */
static bool control(sim_t *s, const char *line)
{
    char word[16], arg[16];
    if (sscanf(line, "%15s %15s", word, arg) != 2) {
        return false;
    }
    char *end = NULL;
    const unsigned long v = strtoul(arg, &end, 0);
    const bool numeric = end != NULL && *end == '\0';
    if (strcmp(word, "ready") == 0 && numeric) {
        s->ready = v != 0u;
    } else if (strcmp(word, "fail") == 0 && numeric) {
        s->fail_skip = 0;
        s->fail_next = (unsigned)v;
    } else if (strcmp(word, "failat") == 0 && numeric && v >= 1u) {
        s->fail_skip = (unsigned)v - 1u;
        s->fail_next = 1;
    } else if (strcmp(word, "delay") == 0 && numeric && v <= 10000u) {
        s->delay_ms = (unsigned)v;
    } else if (strcmp(word, "leds") == 0 && numeric && v <= 0xFFu) {
        s->leds = (uint8_t)v;
    } else if (strcmp(word, "reject") == 0) {
        if (strcmp(arg, "none") == 0) {
            s->reject = 0;
            return true;
        }
        for (unsigned k = 0; k < K_COUNT; k++) {
            if (strcmp(arg, KIND_NAMES[k]) == 0) {
                s->reject |= 1u << k;
                return true;
            }
        }
        return false;
    } else {
        return false;
    }
    return true;
}

/* Read stdin, apply complete lines. Returns false when stdin is closed. */
static bool read_controls(sim_t *s, char *buf, size_t cap, size_t *used)
{
    const ssize_t n = read(STDIN_FILENO, buf + *used, cap - 1u - *used);
    if (n <= 0) {
        return false;
    }
    *used += (size_t)n;
    buf[*used] = '\0';
    char *line = buf;
    char *nl;
    while ((nl = strchr(line, '\n')) != NULL) {
        *nl = '\0';
        if (*line != '\0') {
            printf("%s %s\n", control(s, line) ? "ok" : "error", line);
            fflush(stdout);
        }
        line = nl + 1;
    }
    const size_t rest = *used - (size_t)(line - buf);
    memmove(buf, line, rest);
    *used = rest;
    if (*used >= cap - 1u) {
        *used = 0; /* an absurdly long line: drop it */
    }
    return true;
}

static void boot(ch9329_core_t *core, const ch9329_sink_t *sink)
{
    ch9329_core_init(core, sink, NULL);
    const hid_profile_t profile = ch9329_profile_for_work_mode(ch9329_cfg_work_mode(core->active.cfg));
    ch9329_core_set_link_info(core, CH9329_OUTPUT_SIM,
                              (uint8_t)hid_profile_collections(profile, HID_POINTERS_REL_AND_ABS));
}

int main(int argc, char **argv)
{
    if (argc < 2) {
        fprintf(stderr, "usage: %s EVENTS_FILE [--not-ready]\n", argv[0]);
        return 2;
    }
    static sim_t sim;
    sim.ready = !(argc > 2 && strcmp(argv[2], "--not-ready") == 0);
    sim.events = fopen(argv[1], "a");
    if (sim.events == NULL) {
        perror("sim_bridge: events file");
        return 1;
    }
    sim.master = posix_openpt(O_RDWR | O_NOCTTY);
    if (sim.master < 0 || grantpt(sim.master) != 0 || unlockpt(sim.master) != 0) {
        perror("sim_bridge: pty");
        return 1;
    }
    const char *slave_name = ptsname(sim.master);
    if (slave_name == NULL) {
        perror("sim_bridge: ptsname");
        return 1;
    }
    /* Keep a slave fd open so reads on the master never fail with EIO between host sessions. */
    int slave = open(slave_name, O_RDWR | O_NOCTTY);
    if (slave < 0) {
        perror("sim_bridge: open slave");
        return 1;
    }
    struct termios t;
    if (tcgetattr(slave, &t) == 0) {
        cfmakeraw(&t);
        tcsetattr(slave, TCSANOW, &t);
    }
    printf("%s\n", slave_name);
    fflush(stdout);

    const ch9329_sink_t sink = {
        .ctx = &sim,
        .send_reply = cb_reply,
        .keyboard_report = cb_kb,
        .mouse_report = cb_mouse,
        .consumer_report = cb_consumer,
        .system_report = cb_system,
        .abs_mouse_report = cb_abs,
        .link_ready = cb_ready,
        .leds = cb_leds,
        .persist_load = cb_load,
        .persist_store = cb_store,
        .request_restart = cb_restart,
        .clock_ms = cb_clock,
        .sleep_until_ms = cb_sleep_until,
    };
    static ch9329_core_t core;
    boot(&core, &sink);

    static char ctl[256];
    size_t ctl_used = 0;
    for (;;) {
        const uint32_t wait = ch9329_core_ms_until_timeout(&core, core_now(&sim));
        struct pollfd fds[2] = {{.fd = sim.master, .events = POLLIN}, {.fd = STDIN_FILENO, .events = POLLIN}};
        const int timeout = wait == UINT32_MAX ? 1000 : (int)(wait > 1000u ? 1000u : wait);
        const int r = poll(fds, 2, timeout);
        if (r < 0 && errno != EINTR) {
            perror("sim_bridge: poll");
            return 1;
        }
        if (r > 0 && (fds[1].revents & (POLLIN | POLLHUP))) {
            if (!read_controls(&sim, ctl, sizeof(ctl), &ctl_used)) {
                return 0; /* parent closed our stdin: exit */
            }
        }
        if (r > 0 && (fds[0].revents & POLLIN)) {
            uint8_t buf[256];
            const ssize_t n = read(sim.master, buf, sizeof(buf));
            if (n > 0) {
                ch9329_core_feed(&core, buf, (size_t)n, core_now(&sim));
            }
        }
        ch9329_core_poll(&core, core_now(&sim));
        if (sim.restart) {
            sim.restart = false;
            boot(&core, &sink); /* reboot: stored config becomes active */
            fprintf(sim.events, "RESTART work_mode=%02X address=%02X\n", ch9329_cfg_work_mode(core.active.cfg),
                    ch9329_cfg_address(core.active.cfg));
            fflush(sim.events);
        }
    }
}
