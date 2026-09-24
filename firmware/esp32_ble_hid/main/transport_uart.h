/*
 * UART transport for the CH9329 protocol.
 *
 * A dedicated reader task (higher priority than the bridge task, same core) takes bytes out of
 * the UART driver as soon as they arrive, stamps each chunk with its arrival time (esp_timer, ms)
 * and queues it. The bridge task consumes chunks at its own pace. That split matters: the bridge
 * task may block for up to CONFIG_BRIDGE_NOTIFY_WAIT_MS waiting for BLE buffers, and bytes that
 * arrived meanwhile must still be judged by when they ARRIVED, or the rest of a frame would be
 * mistaken for a late byte (a false E1).
 */
#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "esp_err.h"

typedef struct {
    uint32_t t_ms;       /* arrival time (esp_timer milliseconds, wraps like the core expects) */
    bool discontinuity;  /* bytes were lost before this chunk (UART or queue overflow) */
    const uint8_t *data; /* valid until transport_uart_release() */
    size_t len;
    void *item;          /* opaque */
} uart_chunk_t;

/* Install the UART driver at `baud` (8N1) and start the reader task. */
esp_err_t transport_uart_start(uint32_t baud);

/* Wait up to timeout_ms (UINT32_MAX = forever) for the next chunk. */
bool transport_uart_receive(uart_chunk_t *out, uint32_t timeout_ms);
void transport_uart_release(uart_chunk_t *chunk);

/* Queue bytes for transmission (copies them; blocks only if the TX buffer is full). */
void transport_uart_write(const uint8_t *data, size_t len);
/* Wait until everything queued has left the TX pin (used before a restart). */
void transport_uart_wait_tx_done(uint32_t timeout_ms);

/*
 * How much later than the real line gap a chunk's timestamp can make a gap look: bytes wait in
 * the UART FIFO until RX_FULL_THRESH bytes are there or the line has been idle for RX_TOUT
 * character times, then the reader runs. The core adds this to the packet interval.
 */
uint32_t transport_uart_timing_slack_ms(uint32_t baud);

/* Milliseconds from esp_timer, the clock used for chunk timestamps and core polling. */
uint32_t transport_now_ms(void);

typedef struct {
    uint32_t bytes;
    uint32_t chunks;
    uint32_t uart_overflows; /* UART_FIFO_OVF / UART_BUFFER_FULL */
    uint32_t queue_drops;    /* chunks dropped because the bridge task fell too far behind */
    uint32_t line_errors;    /* framing / parity errors (usually a baud mismatch) */
} transport_stats_t;

void transport_uart_stats(transport_stats_t *out);
