/*
 * UART transport: see transport_uart.h for why reading and processing are separate tasks.
 *
 * API usage follows the ESP-IDF "uart_events" example (examples/peripherals/uart/uart_events,
 * Apache-2.0 / CC0); no code was copied.
 */
#include "transport_uart.h"

#include <string.h>

#include "bridge_config.h"
#include "ch9329_proto.h"
#include "driver/uart.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "freertos/ringbuf.h"
#include "freertos/semphr.h"
#include "freertos/task.h"

static const char *TAG = "uart";

#define UART_PORT ((uart_port_t)CONFIG_BRIDGE_UART_PORT_NUM)

/* The RX FIFO raises an interrupt once this many bytes wait in it... */
#define RX_FULL_THRESH 16
/* ...or once the line has been idle for this many character times. */
#define RX_TOUT_SYMBOLS 2
#define UART_RX_BUF 4096       /* driver ring buffer (must exceed the 128-byte hardware FIFO) */
#define UART_TX_BUF 1024       /* replies are at most 70 bytes each */
#define UART_EVENT_QUEUE_LEN 32
#define CHUNK_MAX 128          /* bytes per queued chunk */
#define CHUNK_QUEUE_BYTES 16384 /* ~1.4 s of back-to-back frames at 115200 baud */

typedef struct {
    uint32_t t_ms;
    uint16_t len;
    uint8_t flags;
    uint8_t reserved;
} chunk_hdr_t;

#define CHUNK_F_DISCONTINUITY 0x01u

static QueueHandle_t s_uart_events;
static RingbufHandle_t s_chunks;
static SemaphoreHandle_t s_started;
static esp_err_t s_start_err;
static uint32_t s_baud;
static transport_stats_t s_stats;
static portMUX_TYPE s_stats_lock = portMUX_INITIALIZER_UNLOCKED;

uint32_t transport_now_ms(void)
{
    return (uint32_t)((uint64_t)esp_timer_get_time() / 1000u);
}

uint32_t transport_uart_timing_slack_ms(uint32_t baud)
{
    /* FIFO threshold + idle timeout + 2 characters of margin, plus 2 ms for scheduling. */
    return ch9329_char_time_ms(baud, RX_FULL_THRESH + RX_TOUT_SYMBOLS + 2u) + 2u;
}

static void stat_add(uint32_t *field, uint32_t n)
{
    portENTER_CRITICAL(&s_stats_lock);
    *field += n;
    portEXIT_CRITICAL(&s_stats_lock);
}

void transport_uart_stats(transport_stats_t *out)
{
    portENTER_CRITICAL(&s_stats_lock);
    *out = s_stats;
    portEXIT_CRITICAL(&s_stats_lock);
}

static esp_err_t install_driver(void)
{
    const uart_config_t cfg = {
        .baud_rate = (int)s_baud,
        .data_bits = UART_DATA_8_BITS,
        .parity = UART_PARITY_DISABLE,
        .stop_bits = UART_STOP_BITS_1,
        .flow_ctrl = UART_HW_FLOWCTRL_DISABLE,
        .source_clk = UART_SCLK_DEFAULT,
    };
    esp_err_t err = uart_driver_install(UART_PORT, UART_RX_BUF, UART_TX_BUF, UART_EVENT_QUEUE_LEN, &s_uart_events, 0);
    if (err == ESP_OK) {
        err = uart_param_config(UART_PORT, &cfg);
    }
    if (err == ESP_OK) {
        err = uart_set_pin(UART_PORT, CONFIG_BRIDGE_UART_TX_GPIO, CONFIG_BRIDGE_UART_RX_GPIO, UART_PIN_NO_CHANGE,
                           UART_PIN_NO_CHANGE);
    }
    if (err == ESP_OK) {
        err = uart_set_rx_full_threshold(UART_PORT, RX_FULL_THRESH);
    }
    if (err == ESP_OK) {
        err = uart_set_rx_timeout(UART_PORT, RX_TOUT_SYMBOLS);
    }
    return err;
}

/* Queue one chunk without ever blocking the reader (a blocked reader would stamp later bytes
 * with the wrong time). Returns false if the queue is full. */
static bool push_chunk(uint32_t t_ms, const uint8_t *data, size_t len, bool discontinuity)
{
    uint8_t buf[sizeof(chunk_hdr_t) + CHUNK_MAX];
    if (len > CHUNK_MAX) {
        return false; /* cannot happen: callers read at most CHUNK_MAX bytes */
    }
    const chunk_hdr_t hdr = {
        .t_ms = t_ms,
        .len = (uint16_t)len,
        .flags = discontinuity ? CHUNK_F_DISCONTINUITY : 0u,
    };
    memcpy(buf, &hdr, sizeof(hdr));
    memcpy(buf + sizeof(hdr), data, len);
    return xRingbufferSend(s_chunks, buf, sizeof(hdr) + len, 0) == pdTRUE;
}

static void reader_task(void *arg)
{
    (void)arg;
    /* Installed from this task so the UART interrupt runs on this core, next to this task. */
    s_start_err = install_driver();
    xSemaphoreGive(s_started);
    if (s_start_err != ESP_OK) {
        vTaskDelete(NULL);
        return;
    }
    bool lost = false; /* bytes were dropped since the last queued chunk */
    uint8_t data[CHUNK_MAX];
    for (;;) {
        uart_event_t ev;
        if (xQueueReceive(s_uart_events, &ev, portMAX_DELAY) != pdTRUE) {
            continue;
        }
        const uint32_t t = transport_now_ms();
        switch (ev.type) {
        case UART_DATA: {
            size_t avail = 0;
            if (uart_get_buffered_data_len(UART_PORT, &avail) != ESP_OK) {
                break;
            }
            while (avail > 0u) {
                const size_t want = avail < CHUNK_MAX ? avail : CHUNK_MAX;
                const int n = uart_read_bytes(UART_PORT, data, (uint32_t)want, 0);
                if (n <= 0) {
                    break;
                }
                if (push_chunk(t, data, (size_t)n, lost)) {
                    lost = false;
                    stat_add(&s_stats.chunks, 1);
                } else {
                    lost = true;
                    stat_add(&s_stats.queue_drops, 1);
                }
                stat_add(&s_stats.bytes, (uint32_t)n);
                avail -= (size_t)n;
            }
            break;
        }
        case UART_FIFO_OVF:
        case UART_BUFFER_FULL:
            /* Bytes are gone: drop what is buffered (it has a hole in it) and tell the parser. */
            uart_flush_input(UART_PORT);
            xQueueReset(s_uart_events);
            lost = true;
            stat_add(&s_stats.uart_overflows, 1);
            break;
        case UART_FRAME_ERR:
        case UART_PARITY_ERR:
        case UART_BREAK:
            /* The (possibly corrupted) bytes still arrive as UART_DATA; checksums catch them. */
            stat_add(&s_stats.line_errors, 1);
            break;
        default:
            break;
        }
    }
}

esp_err_t transport_uart_start(uint32_t baud)
{
    s_baud = baud;
    s_chunks = xRingbufferCreate(CHUNK_QUEUE_BYTES, RINGBUF_TYPE_NOSPLIT);
    s_started = xSemaphoreCreateBinary();
    if (s_chunks == NULL || s_started == NULL) {
        return ESP_ERR_NO_MEM;
    }
    if (xTaskCreatePinnedToCore(reader_task, "uart_rx", 4096, NULL, BRIDGE_UART_RX_TASK_PRIO, NULL,
                                BRIDGE_TASK_CORE) != pdPASS) {
        return ESP_ERR_NO_MEM;
    }
    xSemaphoreTake(s_started, portMAX_DELAY);
    if (s_start_err == ESP_OK) {
        ESP_LOGI(TAG, "UART%d at %u baud 8N1 (TX GPIO%d, RX GPIO%d)", CONFIG_BRIDGE_UART_PORT_NUM, (unsigned)baud,
                 CONFIG_BRIDGE_UART_TX_GPIO, CONFIG_BRIDGE_UART_RX_GPIO);
    }
    return s_start_err;
}

bool transport_uart_receive(uart_chunk_t *out, uint32_t timeout_ms)
{
    TickType_t ticks = portMAX_DELAY;
    if (timeout_ms != UINT32_MAX) {
        /* Round up and add a tick: waking early would only cause a useless poll. */
        ticks = pdMS_TO_TICKS(timeout_ms) + 1;
    }
    size_t size = 0;
    void *item = xRingbufferReceive(s_chunks, &size, ticks);
    if (item == NULL) {
        return false;
    }
    chunk_hdr_t hdr;
    if (size < sizeof(hdr)) {
        vRingbufferReturnItem(s_chunks, item); /* cannot happen: every item has a header */
        return false;
    }
    memcpy(&hdr, item, sizeof(hdr));
    const size_t len = size - sizeof(hdr);
    out->t_ms = hdr.t_ms;
    out->discontinuity = (hdr.flags & CHUNK_F_DISCONTINUITY) != 0u;
    out->data = (const uint8_t *)item + sizeof(hdr);
    out->len = len < hdr.len ? len : hdr.len;
    out->item = item;
    return true;
}

void transport_uart_release(uart_chunk_t *chunk)
{
    if (chunk->item != NULL) {
        vRingbufferReturnItem(s_chunks, chunk->item);
        chunk->item = NULL;
    }
}

void transport_uart_write(const uint8_t *data, size_t len)
{
    if (len == 0u) {
        return;
    }
    const int n = uart_write_bytes(UART_PORT, data, len);
    if (n != (int)len) {
        ESP_LOGE(TAG, "UART write failed (%d of %u bytes)", n, (unsigned)len);
    }
}

void transport_uart_wait_tx_done(uint32_t timeout_ms)
{
    (void)uart_wait_tx_done(UART_PORT, pdMS_TO_TICKS(timeout_ms));
}
