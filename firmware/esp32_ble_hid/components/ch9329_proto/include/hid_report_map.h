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
 * BLE: one Report Map for the collections of the active profile, with report ids when it has
 *      several collections and without any when it has one (hid_map_build()).
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
#define HID_ABS_CENTRE 16384u /* idle position of the absolute pointer, see hid_coll_idle_report() */

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

/*
 * The idle input value of collection `c` (nothing pressed, no movement): what a readable input
 * report holds before the first report and after a new connection. Writes hid_coll_input_len(c)
 * bytes and returns that length; 0 for an invalid collection or `cap` too small.
 * All zero, except the absolute pointer: every absolute report is a position, and X = Y = 0
 * sends the phone's pointer to the top-left corner. Its idle value is the centre of the screen
 * (HID_ABS_CENTRE, HID_ABS_CENTRE) with no button and no wheel movement.
 */
size_t hid_coll_idle_report(hid_coll_t c, uint8_t *out, size_t cap);

/* ---- BLE Report Map ------------------------------------------------------------------------ */

/*
 * HID over GATT carries one Report Map for every collection. With several collections, each one
 * has its Report ID and its characteristics' Report Reference descriptors say that id. With
 * exactly one collection, the map has NO Report ID item and the Report References say id 0.
 * Reason: on iOS 13.2.3 a single-collection map that declared a Report ID had its notifications
 * ignored; removing the Report ID item fixed it, and maps with several collections, each with an
 * id, worked (Apple Developer Forums thread 126757).
 */
bool hid_map_uses_ids(unsigned mask);
/*
 * Report Reference id of collection `c` in the map of `mask`: 0 when `c` is the map's only
 * collection, otherwise hid_coll_report_id(c). A collection outside `mask` keeps its own id (not
 * in the map, never notified), so no two report characteristics share an id and a type.
 */
uint8_t hid_map_report_id(unsigned mask, hid_coll_t c);
/* hid_desc_build(out, cap, mask, hid_map_uses_ids(mask)). */
size_t hid_map_build(uint8_t *out, size_t cap, unsigned mask);

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
    HID_POINTERS_COUNT = 3,
} hid_pointers_t;

hid_profile_t ch9329_profile_for_work_mode(uint8_t work_mode);
const char *hid_profile_name(hid_profile_t profile);
/* Collections of a profile given the pointer selection. Never empty. */
unsigned hid_profile_collections(hid_profile_t profile, hid_pointers_t pointers);

/* ---- descriptor identity ------------------------------------------------------------------- */

/*
 * iOS caches HID descriptors: over USB by device identity (VID, PID, serial number), over BLE for
 * the life of the bond. A phone that saw one build may apply that cached descriptor to another
 * build with other collections. The firmware therefore appends a tag naming the collection set
 * to the device name (BLE name, USB product string) and to the serial number:
 *
 *   composite (0x00/0x03)  "RA" relative + absolute   "R" relative only   "A" absolute only
 *   keyboard-only (0x01)   "K" (no pointer: the same descriptor whatever the pointer choice)
 *   mouse-only (0x02)      "MRA"                      "MR"                "MA"
 *
 * Two builds share a tag exactly when they expose the same collections. A new USB interface
 * order or report layout needs a new PID as well (Kconfig); over BLE the tag makes a stale
 * pairing visible, but only Forget + re-pairing clears iOS's cache (see README).
 * Out-of-range arguments are treated like hid_profile_collections() treats them.
 */
#define HID_IDENTITY_TAG_MAX 3u
const char *hid_identity_tag(hid_profile_t profile, hid_pointers_t pointers);
/*
 * "<base>-<tag>" into `out`, NUL-terminated (just "<tag>" if `base` is NULL or empty). `base` is
 * shortened when needed so the tag always fits. Returns the length, or 0 (out = "" when cap > 0)
 * if `cap` cannot hold "-<tag>" and the NUL.
 */
size_t hid_identity_string(char *out, size_t cap, const char *base, hid_profile_t profile, hid_pointers_t pointers);

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
