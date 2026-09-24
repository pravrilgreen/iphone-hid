/*
 * HID report descriptors (see hid_report_map.h). Written from the HID 1.11 specification and
 * the HID Usage Tables.
 *
 * Consumer usages (HID Usage Tables, Consumer page 0x0C), in CH9329 multimedia bit order:
 *
 *   bit  CH9329 name     usage   HID name
 *    0   Volume+         0x0E9   Volume Increment
 *    1   Volume-         0x0EA   Volume Decrement
 *    2   Mute            0x0E2   Mute
 *    3   Play/Pause      0x0CD   Play/Pause
 *    4   Next Track      0x0B5   Scan Next Track
 *    5   Prev Track      0x0B6   Scan Previous Track
 *    6   CD Stop         0x0B7   Stop
 *    7   Eject           0x0B8   Eject
 *    8   E-Mail          0x18A   AL Email Reader
 *    9   WWW Search      0x221   AC Search
 *   10   WWW Favorites   0x22A   AC Bookmarks
 *   11   WWW Home        0x223   AC Home
 *   12   WWW Back        0x224   AC Back
 *   13   WWW Forward     0x225   AC Forward
 *   14   WWW Stop        0x226   AC Stop
 *   15   Refresh         0x227   AC Refresh
 *   16   Media           0x183   AL Consumer Control Configuration (the "media player" launcher)
 *   17   Explorer        0x196   AL Internet Browser (best guess: the WCH table does not say)
 *   18   Calculator      0x192   AL Calculator
 *   19   Screen Save     0x1B1   AL Screen Saver
 *   20   My Computer     0x194   AL Local Machine Browser
 *   21   Minimize        0x206   AC Minimize
 *   22   Record          0x0B2   Record
 *   23   Rewind          0x0B4   Rewind
 *
 * What iOS does with each usage is not documented; volume, mute, play/pause and track keys are
 * the ones known to work on iPhones with Bluetooth keyboards.
 */
#include "hid_report_map.h"

#include <string.h>

#include "ch9329_proto.h"

#define CONSUMER_USAGE_LIST(X)                                                                        \
    X(0x00E9) X(0x00EA) X(0x00E2) X(0x00CD) X(0x00B5) X(0x00B6) X(0x00B7) X(0x00B8)                  \
    X(0x018A) X(0x0221) X(0x022A) X(0x0223) X(0x0224) X(0x0225) X(0x0226) X(0x0227)                  \
    X(0x0183) X(0x0196) X(0x0192) X(0x01B1) X(0x0194) X(0x0206) X(0x00B2) X(0x00B4)

/* Every consumer usage as a 2-byte local Usage item (tag 0x0A): valid for all values. */
#define AS_USAGE_ITEM(u) 0x0A, (uint8_t)((u) & 0xFFu), (uint8_t)(((u) >> 8) & 0xFFu),
#define AS_VALUE(u) (u),

const uint16_t hid_consumer_usages[HID_CONSUMER_BITS] = {CONSUMER_USAGE_LIST(AS_VALUE)};

/*
 * Every collection below starts with the same 6 bytes (Usage Page, Usage, Collection
 * (Application)); the Report ID item, when wanted, is inserted right after them.
 */
#define HEAD_LEN 6u

/* Keyboard. Input: modifiers, reserved, 6-key array. Output: 5 LED bits + padding. */
static const uint8_t DESC_KEYBOARD[] = {
    0x05, 0x01,             /* Usage Page (Generic Desktop) */
    0x09, 0x06,             /* Usage (Keyboard) */
    0xA1, 0x01,             /* Collection (Application) */
    0x05, 0x07,             /*   Usage Page (Keyboard/Keypad) */
    0x19, 0xE0,             /*   Usage Minimum (Left Control) */
    0x29, 0xE7,             /*   Usage Maximum (Right GUI) */
    0x15, 0x00,             /*   Logical Minimum (0) */
    0x25, 0x01,             /*   Logical Maximum (1) */
    0x75, 0x01,             /*   Report Size (1) */
    0x95, 0x08,             /*   Report Count (8) */
    0x81, 0x02,             /*   Input (Data,Var,Abs): modifier bits */
    0x95, 0x01,             /*   Report Count (1) */
    0x75, 0x08,             /*   Report Size (8) */
    0x81, 0x01,             /*   Input (Const): reserved byte */
    0x05, 0x08,             /*   Usage Page (LEDs) */
    0x19, 0x01,             /*   Usage Minimum (Num Lock) */
    0x29, 0x05,             /*   Usage Maximum (Kana) */
    0x95, 0x05,             /*   Report Count (5) */
    0x75, 0x01,             /*   Report Size (1) */
    0x91, 0x02,             /*   Output (Data,Var,Abs): LED bits */
    0x95, 0x01,             /*   Report Count (1) */
    0x75, 0x03,             /*   Report Size (3) */
    0x91, 0x01,             /*   Output (Const): padding */
    0x05, 0x07,             /*   Usage Page (Keyboard/Keypad) */
    0x19, 0x00,             /*   Usage Minimum (0) */
    0x2A, 0xFF, 0x00,       /*   Usage Maximum (255) */
    0x15, 0x00,             /*   Logical Minimum (0) */
    0x26, 0xFF, 0x00,       /*   Logical Maximum (255) */
    0x95, 0x06,             /*   Report Count (6) */
    0x75, 0x08,             /*   Report Size (8) */
    0x81, 0x00,             /*   Input (Data,Array,Abs): key usages */
    0xC0,                   /* End Collection */
};

/* Relative mouse: 3 buttons + 5 bits padding, X, Y, wheel relative int8 -127..127. */
static const uint8_t DESC_MOUSE[] = {
    0x05, 0x01,             /* Usage Page (Generic Desktop) */
    0x09, 0x02,             /* Usage (Mouse) */
    0xA1, 0x01,             /* Collection (Application) */
    0x09, 0x01,             /*   Usage (Pointer) */
    0xA1, 0x00,             /*   Collection (Physical) */
    0x05, 0x09,             /*     Usage Page (Button) */
    0x19, 0x01,             /*     Usage Minimum (1) */
    0x29, 0x03,             /*     Usage Maximum (3) */
    0x15, 0x00,             /*     Logical Minimum (0) */
    0x25, 0x01,             /*     Logical Maximum (1) */
    0x95, 0x03,             /*     Report Count (3) */
    0x75, 0x01,             /*     Report Size (1) */
    0x81, 0x02,             /*     Input (Data,Var,Abs): buttons */
    0x95, 0x01,             /*     Report Count (1) */
    0x75, 0x05,             /*     Report Size (5) */
    0x81, 0x03,             /*     Input (Const,Var,Abs): padding */
    0x05, 0x01,             /*     Usage Page (Generic Desktop) */
    0x09, 0x30,             /*     Usage (X) */
    0x09, 0x31,             /*     Usage (Y) */
    0x09, 0x38,             /*     Usage (Wheel) */
    0x15, 0x81,             /*     Logical Minimum (-127) */
    0x25, 0x7F,             /*     Logical Maximum (127) */
    0x75, 0x08,             /*     Report Size (8) */
    0x95, 0x03,             /*     Report Count (3) */
    0x81, 0x06,             /*     Input (Data,Var,Rel): X, Y, wheel */
    0xC0,                   /*   End Collection */
    0xC0,                   /* End Collection */
};

/* Consumer control: 24 one-bit usages in CH9329 bit order. */
static const uint8_t DESC_CONSUMER[] = {
    0x05, 0x0C,             /* Usage Page (Consumer) */
    0x09, 0x01,             /* Usage (Consumer Control) */
    0xA1, 0x01,             /* Collection (Application) */
    0x15, 0x00,             /*   Logical Minimum (0) */
    0x25, 0x01,             /*   Logical Maximum (1) */
    0x75, 0x01,             /*   Report Size (1) */
    0x95, 0x18,             /*   Report Count (24) */
    CONSUMER_USAGE_LIST(AS_USAGE_ITEM) /* 24 x Usage (...) */
    0x81, 0x02,             /*   Input (Data,Var,Abs) */
    0xC0,                   /* End Collection */
};

/* System control: the CH9329 ACPI keys. */
static const uint8_t DESC_SYSTEM[] = {
    0x05, 0x01,             /* Usage Page (Generic Desktop) */
    0x09, 0x80,             /* Usage (System Control) */
    0xA1, 0x01,             /* Collection (Application) */
    0x15, 0x00,             /*   Logical Minimum (0) */
    0x25, 0x01,             /*   Logical Maximum (1) */
    0x75, 0x01,             /*   Report Size (1) */
    0x95, 0x03,             /*   Report Count (3) */
    0x09, 0x81,             /*   Usage (System Power Down) */
    0x09, 0x82,             /*   Usage (System Sleep) */
    0x09, 0x83,             /*   Usage (System Wake Up) */
    0x81, 0x02,             /*   Input (Data,Var,Abs) */
    0x95, 0x01,             /*   Report Count (1) */
    0x75, 0x05,             /*   Report Size (5) */
    0x81, 0x03,             /*   Input (Const,Var,Abs): padding */
    0xC0,                   /* End Collection */
};

/* Absolute pointer: 3 buttons + padding, X/Y 16-bit absolute 0..32767, relative wheel. */
static const uint8_t DESC_ABS_POINTER[] = {
    0x05, 0x01,             /* Usage Page (Generic Desktop) */
    0x09, 0x02,             /* Usage (Mouse) */
    0xA1, 0x01,             /* Collection (Application) */
    0x09, 0x01,             /*   Usage (Pointer) */
    0xA1, 0x00,             /*   Collection (Physical) */
    0x05, 0x09,             /*     Usage Page (Button) */
    0x19, 0x01,             /*     Usage Minimum (1) */
    0x29, 0x03,             /*     Usage Maximum (3) */
    0x15, 0x00,             /*     Logical Minimum (0) */
    0x25, 0x01,             /*     Logical Maximum (1) */
    0x95, 0x03,             /*     Report Count (3) */
    0x75, 0x01,             /*     Report Size (1) */
    0x81, 0x02,             /*     Input (Data,Var,Abs): buttons */
    0x95, 0x01,             /*     Report Count (1) */
    0x75, 0x05,             /*     Report Size (5) */
    0x81, 0x03,             /*     Input (Const,Var,Abs): padding */
    0x05, 0x01,             /*     Usage Page (Generic Desktop) */
    0x09, 0x30,             /*     Usage (X) */
    0x09, 0x31,             /*     Usage (Y) */
    0x16, 0x00, 0x00,       /*     Logical Minimum (0) */
    0x26, 0xFF, 0x7F,       /*     Logical Maximum (32767) */
    0x75, 0x10,             /*     Report Size (16) */
    0x95, 0x02,             /*     Report Count (2) */
    0x81, 0x02,             /*     Input (Data,Var,Abs): X, Y */
    0x09, 0x38,             /*     Usage (Wheel) */
    0x15, 0x81,             /*     Logical Minimum (-127) */
    0x25, 0x7F,             /*     Logical Maximum (127) */
    0x75, 0x08,             /*     Report Size (8) */
    0x95, 0x01,             /*     Report Count (1) */
    0x81, 0x06,             /*     Input (Data,Var,Rel): wheel */
    0xC0,                   /*   End Collection */
    0xC0,                   /* End Collection */
};

typedef struct {
    const uint8_t *desc;
    size_t len;
    uint8_t report_id;
    uint8_t input_len;
    const char *name;
} coll_info_t;

static const coll_info_t COLLS[HID_COLL_COUNT] = {
    [HID_COLL_KEYBOARD] = {DESC_KEYBOARD, sizeof(DESC_KEYBOARD), HID_REPORT_ID_KEYBOARD, CH9329_KB_REPORT_LEN,
                           "keyboard"},
    [HID_COLL_MOUSE] = {DESC_MOUSE, sizeof(DESC_MOUSE), HID_REPORT_ID_MOUSE, CH9329_MOUSE_REPORT_LEN, "mouse"},
    [HID_COLL_CONSUMER] = {DESC_CONSUMER, sizeof(DESC_CONSUMER), HID_REPORT_ID_CONSUMER, CH9329_CONSUMER_REPORT_LEN,
                           "consumer"},
    [HID_COLL_SYSTEM] = {DESC_SYSTEM, sizeof(DESC_SYSTEM), HID_REPORT_ID_SYSTEM, CH9329_SYSTEM_REPORT_LEN, "system"},
    [HID_COLL_ABS_POINTER] = {DESC_ABS_POINTER, sizeof(DESC_ABS_POINTER), HID_REPORT_ID_ABS_POINTER,
                              HID_ABS_REPORT_LEN, "abs-pointer"},
};

_Static_assert(sizeof(DESC_KEYBOARD) + sizeof(DESC_MOUSE) + sizeof(DESC_CONSUMER) + sizeof(DESC_SYSTEM) +
                       sizeof(DESC_ABS_POINTER) + 2u * HID_COLL_COUNT <=
                   HID_DESC_MAX,
               "HID_DESC_MAX too small");
/* BLE caps an attribute value at 512 bytes. */
_Static_assert(HID_DESC_MAX <= 512u, "report map too large for a GATT attribute");
_Static_assert(HID_ABS_REPORT_LEN == CH9329_ABS_REPORT_LEN, "absolute report layout mismatch");

uint8_t hid_coll_report_id(hid_coll_t c)
{
    return (unsigned)c < HID_COLL_COUNT ? COLLS[c].report_id : 0u;
}

uint8_t hid_coll_input_len(hid_coll_t c)
{
    return (unsigned)c < HID_COLL_COUNT ? COLLS[c].input_len : 0u;
}

const char *hid_coll_name(hid_coll_t c)
{
    return (unsigned)c < HID_COLL_COUNT ? COLLS[c].name : "?";
}

size_t hid_desc_build(uint8_t *out, size_t cap, unsigned mask, bool with_ids)
{
    if (out == NULL || mask == 0u || (mask & ~HID_COLL_ALL) != 0u) {
        return 0;
    }
    size_t n = 0;
    for (unsigned c = 0; c < HID_COLL_COUNT; c++) {
        if ((mask & HID_COLL_BIT(c)) == 0u) {
            continue;
        }
        const coll_info_t *ci = &COLLS[c];
        const size_t need = ci->len + (with_ids ? 2u : 0u);
        if (need > cap - n) { /* n <= cap always holds, so this cannot wrap */
            return 0;
        }
        memcpy(&out[n], ci->desc, HEAD_LEN);
        n += HEAD_LEN;
        if (with_ids) {
            out[n++] = 0x85; /* Report ID */
            out[n++] = ci->report_id;
        }
        memcpy(&out[n], ci->desc + HEAD_LEN, ci->len - HEAD_LEN);
        n += ci->len - HEAD_LEN;
    }
    return n;
}

hid_profile_t ch9329_profile_for_work_mode(uint8_t work_mode)
{
    switch (work_mode) {
    case CH9329_WORK_MODE_KEYBOARD:
        return HID_PROFILE_KEYBOARD;
    case CH9329_WORK_MODE_MOUSE:
        return HID_PROFILE_MOUSE;
    case CH9329_WORK_MODE_COMPOSITE:
    case CH9329_WORK_MODE_CUSTOM:
    default:
        return HID_PROFILE_COMPOSITE;
    }
}

const char *hid_profile_name(hid_profile_t profile)
{
    switch (profile) {
    case HID_PROFILE_KEYBOARD:
        return "keyboard-only";
    case HID_PROFILE_MOUSE:
        return "mouse-only";
    case HID_PROFILE_COMPOSITE:
        return "composite";
    default:
        return "?";
    }
}

unsigned hid_profile_collections(hid_profile_t profile, hid_pointers_t pointers)
{
    unsigned ptr;
    switch (pointers) {
    case HID_POINTERS_REL_ONLY:
        ptr = HID_COLL_BIT(HID_COLL_MOUSE);
        break;
    case HID_POINTERS_ABS_ONLY:
        ptr = HID_COLL_BIT(HID_COLL_ABS_POINTER);
        break;
    case HID_POINTERS_REL_AND_ABS:
    default:
        ptr = HID_COLL_BIT(HID_COLL_MOUSE) | HID_COLL_BIT(HID_COLL_ABS_POINTER);
        break;
    }
    switch (profile) {
    case HID_PROFILE_KEYBOARD:
        return HID_COLL_BIT(HID_COLL_KEYBOARD);
    case HID_PROFILE_MOUSE:
        return ptr;
    case HID_PROFILE_COMPOSITE:
    default:
        return HID_COLL_BIT(HID_COLL_KEYBOARD) | HID_COLL_BIT(HID_COLL_CONSUMER) | HID_COLL_BIT(HID_COLL_SYSTEM) | ptr;
    }
}
