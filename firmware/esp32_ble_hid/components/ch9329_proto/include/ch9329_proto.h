/*
 * CH9329 serial frame protocol - portable core.
 *
 * This component has no ESP-IDF dependency: it only needs a C99 compiler and <string.h>.
 * It is built into the firmware and, unchanged, into the host unit tests (test_host/).
 *
 * Frame (host -> bridge and bridge -> host):
 *
 *     57 AB | ADDR | CMD | LEN | DATA[LEN] | SUM        SUM = (sum of every previous byte) & 0xFF
 *
 * Replies echo the command as CMD | 0x80 (success) or CMD | 0xC0 (error, DATA = 1 status byte).
 * A frame addressed to 0xFF (broadcast) is executed but never answered.
 *
 * Data flow:
 *
 *     transport bytes --> ch9329_core_feed() --> parser --> dispatcher --> ch9329_sink_t callbacks
 *                                                                  \--> sink.send_reply(frame)
 *
 * Time is never read from a clock: every entry point takes `now_ms` (any monotonic millisecond
 * counter; wrap-around after 2^32 ms is handled). That keeps the core deterministic and testable.
 * `now_ms` passed to ch9329_core_feed() is the time the bytes ARRIVED, not the time they are
 * processed: the firmware timestamps UART data in a separate reader task, so a bridge task that
 * was busy (e.g. waiting for BLE buffers) never mistakes its own delay for a gap on the line.
 *
 * Threading: a ch9329_core_t is not thread-safe. Call every function for one core from the same
 * task (the firmware's bridge task). Sink callbacks run synchronously inside those calls, in
 * stream order, so HID reports reach the sink in exactly the order the host sent them and each
 * reply is emitted only after its report callback has returned.
 */
#ifndef CH9329_PROTO_H
#define CH9329_PROTO_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* ---- frame layout ------------------------------------------------------------------------ */

#define CH9329_HEAD0 0x57u
#define CH9329_HEAD1 0xABu
#define CH9329_MAX_DATA 64u
#define CH9329_OVERHEAD 6u /* 2 header + addr + cmd + len + sum */
#define CH9329_MAX_FRAME (CH9329_MAX_DATA + CH9329_OVERHEAD)
#define CH9329_ADDR_BROADCAST 0xFFu
#define CH9329_REPLY_OK_FLAG 0x80u
#define CH9329_REPLY_ERR_FLAG 0xC0u

/*
 * GET_INFO byte 0. A real CH9329 answers 0x30.. ("V1.x"); the bridge answers 0x40 = "ihc bridge
 * v1.0" so the host can tell them apart (ihc/hid/ch9329.py shows it as "unknown (0x40)" and
 * works unchanged). With version 0x40, the reserved GET_INFO bytes carry bridge details:
 *
 *   byte 3  output: 0x01 Bluetooth LE, 0x02 USB (TinyUSB), 0x7F host simulator, 0x00 unknown
 *   byte 4  HID collections of the running profile: bit0 keyboard, bit1 relative mouse,
 *           bit2 consumer, bit3 system, bit4 absolute pointer (hid_report_map.h HID_COLL_*)
 *   byte 5  vendor features: bit0 SEND_MS_REL_RUN (0x30), bit1 its quarter-millisecond interval
 *           flag, bit2 its E7 "played off schedule" status (all three, or none without a clock)
 *   byte 6-7 report period: how often the phone actually takes a HID report, u16 little-endian
 *           (byte 6 low) in units of 0.25 ms; 0 = unknown / not connected. BLE: the current
 *           connection interval (60 = 15 ms); USB: the interrupt IN endpoints' polling interval
 *           (4 = 1 ms). Read on every GET_INFO: a BLE link can renegotiate its interval. The host
 *           paces pointer reports at a multiple of it, so the phone sees evenly spaced reports.
 */
#define CH9329_BRIDGE_VERSION 0x40u
#define CH9329_OUTPUT_UNKNOWN 0x00u
#define CH9329_OUTPUT_BLE 0x01u
#define CH9329_OUTPUT_USB 0x02u
#define CH9329_OUTPUT_SIM 0x7Fu
#define CH9329_FEATURE_REL_RUN 0x01u            /* SEND_MS_REL_RUN */
#define CH9329_FEATURE_REL_RUN_QUARTER_MS 0x02u /* ... interval in 0.25 ms units (flags bit 7) */
#define CH9329_FEATURE_REL_RUN_LATE 0x04u       /* ... answers E7 when a report went out late */
#define CH9329_PERIOD_UNITS_PER_MS 4u /* GET_INFO bytes 6-7 count 0.25 ms */

enum ch9329_cmd {
    CH9329_CMD_GET_INFO = 0x01,
    CH9329_CMD_SEND_KB_GENERAL_DATA = 0x02,
    CH9329_CMD_SEND_KB_MEDIA_DATA = 0x03,
    CH9329_CMD_SEND_MS_ABS_DATA = 0x04,
    CH9329_CMD_SEND_MS_REL_DATA = 0x05,
    CH9329_CMD_SEND_MY_HID_DATA = 0x06, /* custom HID: not supported by the bridge (E3) */
    CH9329_CMD_GET_PARA_CFG = 0x08,
    CH9329_CMD_SET_PARA_CFG = 0x09,
    CH9329_CMD_GET_USB_STRING = 0x0A,
    CH9329_CMD_SET_USB_STRING = 0x0B,
    CH9329_CMD_SET_DEFAULT_CFG = 0x0C,
    CH9329_CMD_RESET = 0x0F,
    /*
     * Vendor extension (not in the WCH protocol; a real CH9329 answers E3):
     * SEND_MS_REL_RUN  dx int8, dy int8, count u8 (1..255), interval u8, flags u8
     *   flags bit 0-2  buttons (bit0 L, bit1 R, bit2 M)
     *   flags bit 3-6  reserved, must be 0 (else E5)
     *   flags bit 7    interval in 0.25 ms units (0..63.75 ms); clear: whole milliseconds
     *                  (0..255 ms), the original encoding, so old hosts are unaffected
     * Emits `count` relative mouse reports {buttons, dx, dy, 0}, report i at t0 + i * interval
     * (t0 = when the frame is executed), timed on the bridge's microsecond clock. The run owns
     * `count` slots: it ends at t0 + count * interval, so a run queued behind it starts exactly
     * one interval after this run's last report (the host sends a whole move as back-to-back
     * runs). A slot that has passed (slow link) is sent at once, never skipped; the schedule is
     * not shifted.
     * Replies once, after the last slot:
     *   00  every report was delivered (same rule as SEND_MS_REL_DATA) and each one was
     *       accepted by the link at most ch9329_options_t.run_late_us after its slot;
     *   E7  every report was delivered, but at least one was accepted later than that: the
     *       phone saw uneven spacing, so the host must not take the landing point as exact;
     *   E6  a report was not delivered: the run stopped there, the rest were not sent (E6 wins
     *       over E7);
     *   E5  count 0, a reserved flag bit set, or (count - 1) * interval > 2000 ms (real time,
     *       whichever unit).
     */
    CH9329_CMD_SEND_MS_REL_RUN = 0x30,
};
#define CH9329_REL_RUN_MAX_MS 2000u
#define CH9329_REL_RUN_BUTTONS 0x07u    /* flags: buttons */
#define CH9329_REL_RUN_RESERVED 0x78u   /* flags: must be 0 */
#define CH9329_REL_RUN_QUARTER_MS 0x80u /* flags: interval counts 0.25 ms */
/* Default late threshold (ch9329_options_t.run_late_us): two 1 ms FreeRTOS ticks, see README. */
#define CH9329_RUN_LATE_DEFAULT_US 2000u

enum ch9329_status {
    CH9329_STATUS_OK = 0x00,
    CH9329_STATUS_TIMEOUT = 0xE1,   /* partial frame: no byte for packet_interval ms */
    CH9329_STATUS_BAD_HEADER = 0xE2, /* never sent by the bridge: bytes before a header are skipped */
    CH9329_STATUS_BAD_CMD = 0xE3,
    CH9329_STATUS_BAD_SUM = 0xE4,
    CH9329_STATUS_BAD_PARAM = 0xE5,
    CH9329_STATUS_EXEC_FAILED = 0xE6,
    /* Vendor (SEND_MS_REL_RUN only): every report delivered, at least one of them late. */
    CH9329_STATUS_RUN_LATE = 0xE7,
};

/* SEND_KB_MEDIA_DATA sub-report ids (first data byte). */
#define CH9329_MEDIA_ACPI 0x01u
#define CH9329_MEDIA_MULTIMEDIA 0x02u
#define CH9329_ACPI_MASK 0x07u /* bit0 power, bit1 sleep, bit2 wake */

/* ---- 50-byte parameter block (GET_PARA_CFG / SET_PARA_CFG) -------------------------------- */

#define CH9329_CFG_SIZE 50u
#define CH9329_CFG_OFF_WORK_MODE 0u
#define CH9329_CFG_OFF_SERIAL_MODE 1u
#define CH9329_CFG_OFF_ADDRESS 2u
#define CH9329_CFG_OFF_BAUD 3u /* 4 bytes, big-endian */
#define CH9329_CFG_OFF_PACKET_INTERVAL 9u /* 2 bytes, big-endian, ms */
#define CH9329_CFG_OFF_VID 11u
#define CH9329_CFG_OFF_PID 13u
#define CH9329_CFG_OFF_KB_UPLOAD_INTERVAL 15u
#define CH9329_CFG_OFF_KB_RELEASE_DELAY 17u
#define CH9329_CFG_OFF_AUTO_ENTER 19u
#define CH9329_CFG_OFF_ENTER_CHARS 20u /* 8 bytes */
#define CH9329_CFG_OFF_FILTER 28u /* 8 bytes */
#define CH9329_CFG_OFF_USB_STR_FLAGS 36u
#define CH9329_CFG_OFF_KB_FAST_UPLOAD 37u

/* Work modes (byte 0). The bridge accepts 0x00-0x03 on write; 0x03 (custom HID) behaves like 0x00. */
#define CH9329_WORK_MODE_COMPOSITE 0x00u /* keyboard + media + mouse */
#define CH9329_WORK_MODE_KEYBOARD 0x01u /* keyboard only (no media, no mouse) */
#define CH9329_WORK_MODE_MOUSE 0x02u /* mouse only */
#define CH9329_WORK_MODE_CUSTOM 0x03u /* custom HID on the real chip; composite on the bridge */

/* Defaults (SET_DEFAULT_CFG and first boot). */
#define CH9329_DEFAULT_BAUD 9600u
#define CH9329_DEFAULT_PACKET_INTERVAL_MS 3u
#define CH9329_DEFAULT_VID 0x1A86u /* the emulated chip's documented defaults; no USB device uses them */
#define CH9329_DEFAULT_PID 0xE129u

/* ---- USB strings (GET_USB_STRING / SET_USB_STRING) ---------------------------------------- */

#define CH9329_STR_MAX 23u
enum ch9329_string_kind {
    CH9329_STR_VENDOR = 0,  /* bridge: BLE Device Information "Manufacturer Name" */
    CH9329_STR_PRODUCT = 1, /* bridge: BLE device name */
    CH9329_STR_SERIAL = 2,  /* bridge: BLE Device Information "Serial Number" */
    CH9329_STR_COUNT = 3,
};

typedef struct {
    uint8_t len;                     /* 0 = not set: the firmware uses its built-in default */
    char text[CH9329_STR_MAX + 1u];  /* printable ASCII, always NUL-terminated */
} ch9329_string_t;

/* Everything the bridge keeps in flash. Only byte-sized members: no padding, stable layout. */
typedef struct {
    uint8_t cfg[CH9329_CFG_SIZE];
    ch9329_string_t str[CH9329_STR_COUNT];
} ch9329_persist_t;

/* ---- HID report sizes produced by the dispatcher ------------------------------------------ */

#define CH9329_KB_REPORT_LEN 8u       /* modifiers, reserved (always 0), 6 key usages */
#define CH9329_MOUSE_REPORT_LEN 4u    /* buttons (bit0 L, bit1 R, bit2 M), dx, dy, wheel: int8 -127..127 */
#define CH9329_CONSUMER_REPORT_LEN 3u /* 24-bit bitmap, bit n = CH9329 multimedia bit n */
#define CH9329_SYSTEM_REPORT_LEN 1u   /* bit0 power down, bit1 sleep, bit2 wake up */
/* buttons (bit0 L, bit1 R, bit2 M), X lo, X hi, Y lo, Y hi (0..32767, scaled from the CH9329's
 * 0..4095 grid), wheel int8 */
#define CH9329_ABS_REPORT_LEN 6u
#define CH9329_ABS_GRID_MAX 4095u

/* ---- sink: what the core needs from the platform ----------------------------------------- */

/*
 * Every callback is optional (NULL): a NULL report callback makes the command fail with E6,
 * NULL link_ready means "never ready", NULL leds and NULL report_period mean 0, NULL
 * persist_store means "cannot store" (E6), NULL persist_load means "nothing stored".
 *
 * Report callbacks are the ONLY authority on delivery. They return CH9329_STATUS_OK only once
 * the report has really been handed to the HID link for this exact report (BLE: connected,
 * encrypted, the central subscribed to this report, and the stack accepted the notification),
 * and CH9329_STATUS_EXEC_FAILED otherwise. Returning OK for a report that was dropped breaks the
 * bridge's contract with the host. They may block for a short, bounded time (the firmware waits
 * up to CONFIG_BRIDGE_NOTIFY_WAIT_MS for BLE buffers); the serial reply waits for them.
 *
 * link_ready only feeds GET_INFO byte 1 ("the phone would receive every report type of the
 * current work mode"). It is deliberately NOT used to gate HID commands: one report type can be
 * deliverable while another is not, and each command must get its own true answer.
 */
typedef struct {
    void *ctx;
    void (*send_reply)(void *ctx, const uint8_t *frame, size_t len);
    uint8_t (*keyboard_report)(void *ctx, const uint8_t report[CH9329_KB_REPORT_LEN]);
    uint8_t (*mouse_report)(void *ctx, const uint8_t report[CH9329_MOUSE_REPORT_LEN]);
    uint8_t (*consumer_report)(void *ctx, const uint8_t report[CH9329_CONSUMER_REPORT_LEN]);
    uint8_t (*system_report)(void *ctx, const uint8_t report[CH9329_SYSTEM_REPORT_LEN]);
    /* NULL: the platform has no absolute pointer; SEND_MS_ABS_DATA then follows
     * ch9329_options_t.abs_mouse_reject. */
    uint8_t (*abs_mouse_report)(void *ctx, const uint8_t report[CH9329_ABS_REPORT_LEN]);
    bool (*link_ready)(void *ctx);   /* GET_INFO byte 1 only (see above) */
    uint8_t (*leds)(void *ctx);      /* keyboard LED output report: bit0 num, bit1 caps, bit2 scroll */
    /* GET_INFO bytes 6-7: current report period in 0.25 ms units, 0 = unknown / not connected.
     * Asked on every GET_INFO (like leds) because the link can change it at any time. */
    uint16_t (*report_period)(void *ctx);
    bool (*persist_load)(void *ctx, ch9329_persist_t *out);
    bool (*persist_store)(void *ctx, const ch9329_persist_t *p);
    /* RESET was accepted: the platform restarts once the reply (if any) has left the wire. */
    void (*request_restart)(void *ctx);
    /* Clock and sleep for SEND_MS_REL_RUN: any monotonic microsecond counter (wrap-around after
     * 2^32 us is handled). sleep_until_us returns at or after t_us, as close to it as the
     * platform can (the firmware sleeps in 1 ms ticks, then spins on its microsecond timer).
     * Both NULL: the command is answered E3 (not supported) and GET_INFO does not advertise it. */
    uint32_t (*clock_us)(void *ctx);
    void (*sleep_until_us)(void *ctx, uint32_t t_us);
    /* Optional, SEND_MS_REL_RUN lateness: clock_us() value at which the link ACCEPTED the report
     * the last report callback answered OK for (BLE: notification queued for the next
     * connection event; USB: report placed in the endpoint, before the phone read it, since
     * waiting for the next poll is the link's own grid, not lateness). NULL: the core reads
     * clock_us() once the callback has returned. */
    uint32_t (*accepted_us)(void *ctx);
} ch9329_sink_t;

typedef struct {
    /* SEND_MS_ABS_DATA when the sink has no abs_mouse_report: false = reply OK and drop the
     * report (documented exception to the delivery rule), true = reply E5. Work-mode gating
     * (E6 in keyboard-only mode) applies either way. */
    bool abs_mouse_reject;
    /* Added to the configured packet interval before a partial frame is declared timed out.
     * The firmware uses it to cover UART/USB delivery granularity; host tests use 0. */
    uint32_t rx_slack_ms;
    /* SEND_MS_REL_RUN: a report accepted by the link more than this many microseconds after its
     * slot makes the run answer E7. 0 = CH9329_RUN_LATE_DEFAULT_US. */
    uint32_t run_late_us;
} ch9329_options_t;

/* ---- incremental frame parser ------------------------------------------------------------ */

typedef enum {
    CH9329_EV_FRAME = 0, /* complete frame, checksum OK */
    CH9329_EV_BAD_SUM,   /* complete frame, checksum mismatch */
    CH9329_EV_TIMEOUT,   /* partial frame expired; `have` bytes were buffered */
} ch9329_event_type_t;

typedef struct {
    ch9329_event_type_t type;
    uint8_t addr;        /* valid if have >= 3 */
    uint8_t cmd;         /* valid if have >= 4 */
    uint8_t len;         /* FRAME / BAD_SUM only */
    const uint8_t *data; /* FRAME / BAD_SUM only; points into the parser, valid during the callback */
    uint8_t have;        /* bytes of the frame that had been received */
} ch9329_event_t;

typedef void (*ch9329_event_cb)(void *user, const ch9329_event_t *ev);

typedef struct {
    uint32_t frames;          /* valid frames */
    uint32_t bad_sum;         /* complete frames with a wrong checksum */
    uint32_t bad_len;         /* candidate headers dropped because LEN > 64 */
    uint32_t timeouts;        /* partial frames expired */
    uint32_t discarded;       /* bytes thrown away while looking for a header */
} ch9329_parser_stats_t;

typedef struct {
    uint8_t buf[CH9329_MAX_FRAME];
    uint8_t n;            /* bytes in buf; buf always starts with a (partial) header when n > 0 */
    uint32_t last_rx_ms;  /* time of the most recent byte */
    ch9329_parser_stats_t stats;
} ch9329_parser_t;

void ch9329_parser_init(ch9329_parser_t *p);
/* Drop any partial frame (e.g. after a UART overflow). Statistics are kept. */
void ch9329_parser_reset(ch9329_parser_t *p);
/*
 * Feed received bytes. A partial frame older than timeout_ms is expired first (as if polled),
 * so bytes separated by a long gap never merge into one frame. Every event is delivered through
 * cb, in stream order; one call can produce several events.
 */
void ch9329_parser_feed(ch9329_parser_t *p, const uint8_t *data, size_t len, uint32_t now_ms,
                        uint32_t timeout_ms, ch9329_event_cb cb, void *user);
/* Expire a partial frame if no byte arrived for timeout_ms. */
void ch9329_parser_poll(ch9329_parser_t *p, uint32_t now_ms, uint32_t timeout_ms, ch9329_event_cb cb,
                        void *user);
/* Milliseconds until poll() would expire the pending partial frame; UINT32_MAX when idle. */
uint32_t ch9329_parser_ms_until_timeout(const ch9329_parser_t *p, uint32_t now_ms, uint32_t timeout_ms);

/* ---- core: parser + dispatcher + configuration ------------------------------------------- */

typedef struct {
    uint32_t replies;         /* reply frames sent */
    uint32_t ignored_addr;    /* frames for another address */
    uint32_t broadcast;       /* frames executed without reply */
    uint32_t hid_sent;        /* HID reports the sink accepted (answered OK) */
    uint32_t hid_failed;      /* HID commands answered E6 (work mode, link, or sink failure) */
    uint32_t abs_dropped;     /* SEND_MS_ABS_DATA accepted and ignored (no absolute pointer) */
    uint32_t runs;            /* SEND_MS_REL_RUN commands executed completely (00 or E7) */
    uint32_t runs_late;       /* ... of which answered E7 (a report later than run_late_us) */
    uint32_t late_max_us;     /* largest lateness of any run report so far */
} ch9329_core_stats_t;

typedef struct {
    ch9329_sink_t sink;
    ch9329_options_t opt;
    ch9329_parser_t parser;
    ch9329_persist_t active; /* loaded at boot: address, work mode, baud, interval, strings in effect */
    ch9329_persist_t stored; /* flash contents: what GET_* return and SET_* change */
    uint32_t timeout_ms;     /* effective partial-frame timeout */
    bool used_defaults;      /* nothing valid was stored at init */
    uint8_t output;          /* GET_INFO byte 3 (CH9329_OUTPUT_*) */
    uint8_t collections;     /* GET_INFO byte 4 (HID_COLL_* bits of the running profile) */
    ch9329_core_stats_t stats;
} ch9329_core_t;

/*
 * Initialise: loads the persisted block through sink->persist_load (defaults if missing or
 * invalid). `sink` and `opt` are copied; opt may be NULL (defaults: accept+ignore absolute
 * mouse, no slack). Nothing is written to flash here.
 */
void ch9329_core_init(ch9329_core_t *c, const ch9329_sink_t *sink, const ch9329_options_t *opt);
void ch9329_core_feed(ch9329_core_t *c, const uint8_t *data, size_t len, uint32_t now_ms);
void ch9329_core_poll(ch9329_core_t *c, uint32_t now_ms);
uint32_t ch9329_core_ms_until_timeout(const ch9329_core_t *c, uint32_t now_ms);
void ch9329_core_set_rx_slack(ch9329_core_t *c, uint32_t slack_ms);
/* What GET_INFO bytes 3 and 4 report (set by the platform once it knows its profile). Bytes
 * 1, 2 and 6-7 change while running: they come from the sink (link_ready, leds, report_period). */
void ch9329_core_set_link_info(ch9329_core_t *c, uint8_t output, uint8_t collections);
/* Scale a CH9329 absolute coordinate (0..4095) to the HID logical range 0..32767. */
uint16_t ch9329_abs_scale(uint16_t grid);
static inline const ch9329_persist_t *ch9329_core_active(const ch9329_core_t *c) { return &c->active; }

/* ---- helpers (pure) ---------------------------------------------------------------------- */

uint8_t ch9329_checksum(const uint8_t *data, size_t len);
/* Build a frame into out (capacity >= 6 + len). Returns the frame length, 0 if len > 64. */
size_t ch9329_encode(uint8_t *out, size_t cap, uint8_t addr, uint8_t cmd, const uint8_t *data, size_t len);

void ch9329_persist_defaults(ch9329_persist_t *p);
/* SET_PARA_CFG rules: work mode 0x00-0x03, serial mode 0x00, address != 0xFF, supported baud. */
bool ch9329_cfg_valid(const uint8_t cfg[CH9329_CFG_SIZE]);
/* Every string printable ASCII, length <= 23, NUL-terminated at len. */
bool ch9329_strings_valid(const ch9329_persist_t *p);
bool ch9329_baud_supported(uint32_t baud);

uint8_t ch9329_cfg_work_mode(const uint8_t cfg[CH9329_CFG_SIZE]);
uint8_t ch9329_cfg_address(const uint8_t cfg[CH9329_CFG_SIZE]);
uint32_t ch9329_cfg_baud(const uint8_t cfg[CH9329_CFG_SIZE]);
uint16_t ch9329_cfg_u16(const uint8_t cfg[CH9329_CFG_SIZE], size_t off);
void ch9329_cfg_put_u16(uint8_t cfg[CH9329_CFG_SIZE], size_t off, uint16_t v);
void ch9329_cfg_put_u32(uint8_t cfg[CH9329_CFG_SIZE], size_t off, uint32_t v);

/*
 * Time a serial line needs for `chars` characters (8N1 = 10 bits each) at `baud`, rounded up
 * to whole milliseconds. The firmware adds this to the packet interval to cover how the UART
 * driver batches received bytes (see main/transport_uart.c).
 */
uint32_t ch9329_char_time_ms(uint32_t baud, uint32_t chars);

#ifdef __cplusplus
}
#endif

#endif /* CH9329_PROTO_H */
