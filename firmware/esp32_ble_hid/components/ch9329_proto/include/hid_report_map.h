/*
 * HID report descriptors for the bridge's HID collections.
 *
 * Pure data + a small builder, no ESP-IDF dependency, so the host tests can parse and check
 * every variant the firmware can produce.
 *
 * Collections, their report ids (used when a descriptor carries several collections) and input
 * payloads (without the id byte):
 *
 *   KEYBOARD     id 1  input 8 bytes  modifiers, reserved, 6 key usages (0x00-0xFF)
 *                      output 1 byte  LEDs: bit0 num, bit1 caps, bit2 scroll, bit3 compose, bit4 kana
 *   MOUSE        id 2  input 4 bytes  buttons (3 bits), X, Y, wheel: relative int8 -127..127
 *   CONSUMER     id 3  input 3 bytes  24 one-bit usages in CH9329 multimedia bit order
 *   SYSTEM       id 4  input 1 byte   bit0 System Power Down, bit1 System Sleep, bit2 System Wake Up
 *   ABS_POINTER  id 5  input 6 bytes  buttons (3 bits), X lo, X hi, Y lo, Y hi (0..32767), wheel int8
 *
 * The absolute pointer follows the layout that is reported to work with iOS over USB (Mouse
 * application collection > Pointer > Physical collection, 16-bit X/Y with logical range
 * 0..32767, relative wheel), the same structure PiKVM and Aiden use. It was written from the
 * HID 1.11 specification and HID Usage Tables; no code was copied. Whether iOS follows an
 * absolute pointer over Bluetooth LE is unproven.
 *
 * BLE: one Report Map with report ids (collections of the active profile).
 * USB: one HID interface per collection group, without report ids except the "extras"
 *      interface (consumer + system), the layout real keyboards, PiKVM and Aiden use.
 */
#ifndef HID_REPORT_MAP_H
#define HID_REPORT_MAP_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef enum {
    HID_COLL_KEYBOARD = 0,
    HID_COLL_MOUSE,
    HID_COLL_CONSUMER,
    HID_COLL_SYSTEM,
    HID_COLL_ABS_POINTER,
    HID_COLL_COUNT,
} hid_coll_t;

#define HID_COLL_BIT(c) (1u << (unsigned)(c))
#define HID_COLL_ALL ((1u << (unsigned)HID_COLL_COUNT) - 1u)

#define HID_REPORT_ID_KEYBOARD 1u
#define HID_REPORT_ID_MOUSE 2u
#define HID_REPORT_ID_CONSUMER 3u
#define HID_REPORT_ID_SYSTEM 4u
#define HID_REPORT_ID_ABS_POINTER 5u

#define HID_ABS_REPORT_LEN 6u
#define HID_ABS_LOGICAL_MAX 32767u

/* Worst case: every collection with report ids. */
#define HID_DESC_MAX 400u

uint8_t hid_coll_report_id(hid_coll_t c);
/* Input report payload length (without report id); 0 for an invalid collection. */
uint8_t hid_coll_input_len(hid_coll_t c);
const char *hid_coll_name(hid_coll_t c);

/*
 * Write the report descriptor for every collection in `mask`, in hid_coll_t order. With
 * `with_ids`, each collection starts with its Report ID item. Returns the length, or 0 if `mask`
 * is empty or has unknown bits, or `cap` is too small (nothing useful is written then).
 */
size_t hid_desc_build(uint8_t *out, size_t cap, unsigned mask, bool with_ids);

/* ---- profiles: which collections a CH9329 work mode exposes -------------------------------- */

typedef enum {
    HID_PROFILE_COMPOSITE = 0, /* work mode 0x00 and 0x03 */
    HID_PROFILE_KEYBOARD = 1,  /* work mode 0x01: keyboard only (no media, no pointer) */
    HID_PROFILE_MOUSE = 2,     /* work mode 0x02: pointers only */
    HID_PROFILE_COUNT = 3,
} hid_profile_t;

/* Which pointer collections the firmware was built with (Kconfig BRIDGE_POINTERS). */
typedef enum {
    HID_POINTERS_REL_AND_ABS = 0,
    HID_POINTERS_REL_ONLY = 1,
    HID_POINTERS_ABS_ONLY = 2,
} hid_pointers_t;

hid_profile_t ch9329_profile_for_work_mode(uint8_t work_mode);
const char *hid_profile_name(hid_profile_t profile);
/* Collections of a profile given the pointer selection. Never empty. */
unsigned hid_profile_collections(hid_profile_t profile, hid_pointers_t pointers);

/*
 * The keyboard-only profile exists for a reported iOS behaviour: with AssistiveTouch on and a
 * pointing device attached, Cmd/Shift/Option shortcuts are sometimes delivered to SpringBoard
 * instead of the foreground app. Removing every pointer collection avoids it.
 */

/*
 * Consumer-page usage of each bit of the consumer report, index = CH9329 multimedia bit number
 * (bit n = bit n%8 of data byte n/8 of SEND_KB_MEDIA_DATA "02 b1 b2 b3").
 */
#define HID_CONSUMER_BITS 24u
extern const uint16_t hid_consumer_usages[HID_CONSUMER_BITS];

#ifdef __cplusplus
}
#endif

#endif /* HID_REPORT_MAP_H */
