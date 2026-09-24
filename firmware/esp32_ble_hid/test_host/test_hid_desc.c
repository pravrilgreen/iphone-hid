/*
 * Host tests for the HID report descriptors: a small HID descriptor parser computes the size of
 * every report, the logical ranges and relative/absolute flags of the pointer axes, and collects
 * the consumer usages, so a descriptor typo is caught here rather than as "the iPhone ignores
 * the keyboard". Every combination the firmware can build is checked.
 */
#include <string.h>

#include "ch9329_proto.h"
#include "fake_sink.h" /* hex_bytes() */
#include "hid_report_map.h"
#include "tinytest.h"

#define MAX_ID 16

typedef struct {
    int32_t lmin, lmax;
    unsigned size;
    bool relative;
    bool seen;
} axis_t;

typedef struct {
    unsigned in_bits[MAX_ID]; /* index 0 = reports without id */
    unsigned out_bits[MAX_ID];
    unsigned feat_bits[MAX_ID];
    unsigned ids_seen; /* bit n = report id n used by a main item (bit 0: no id) */
    bool id_item;      /* a Report ID item appears anywhere */
    int depth;
    int max_depth;
    int app_collections;
    bool error;
    const char *why;
    uint16_t consumer_usages[64]; /* usages of the Consumer-page Input item, in order */
    size_t n_consumer;
    axis_t x[MAX_ID]; /* Generic Desktop X per report id */
    axis_t wheel[MAX_ID];
} desc_info_t;

static int32_t item_value(const uint8_t *p, unsigned size, bool is_signed)
{
    uint32_t v = 0;
    for (unsigned i = 0; i < size; i++) {
        v |= (uint32_t)p[i] << (8 * i);
    }
    if (is_signed && size > 0 && size < 4 && (v & (1u << (8 * size - 1)))) {
        v |= ~0u << (8 * size);
    }
    return (int32_t)v;
}

static void fail(desc_info_t *out, const char *why)
{
    out->error = true;
    out->why = why;
}

static void parse(const uint8_t *d, size_t len, desc_info_t *out)
{
    memset(out, 0, sizeof(*out));
    unsigned usage_page = 0, report_size = 0, report_count = 0, report_id = 0;
    bool any_id = false;
    int32_t lmin = 0, lmax = 0;
    uint32_t usages[64];
    size_t n_usages = 0;
    size_t i = 0;
    while (i < len) {
        const uint8_t prefix = d[i];
        if (prefix == 0xFE) {
            fail(out, "long item");
            return;
        }
        unsigned size = prefix & 0x03u;
        if (size == 3) {
            size = 4;
        }
        const unsigned type = (prefix >> 2) & 0x03u;
        const unsigned tag = prefix >> 4;
        if (i + 1 + size > len) {
            fail(out, "truncated item");
            return;
        }
        const uint8_t *data = &d[i + 1];
        const int32_t sval = item_value(data, size, true);
        const uint32_t uval = (uint32_t)item_value(data, size, false);
        if (type == 0) { /* main */
            if (tag == 0x8 || tag == 0x9 || tag == 0xB) {
                if (any_id && report_id == 0) {
                    fail(out, "main item without report id after ids were used");
                    return;
                }
                const unsigned bits = report_size * report_count;
                unsigned *acc = tag == 0x8 ? out->in_bits : tag == 0x9 ? out->out_bits : out->feat_bits;
                acc[report_id] += bits;
                out->ids_seen |= 1u << report_id;
                const bool constant = (uval & 0x01u) != 0;
                const bool relative = (uval & 0x04u) != 0;
                if (tag == 0x8 && !constant && usage_page == 0x0C) {
                    for (size_t k = 0; k < n_usages && out->n_consumer < 64; k++) {
                        out->consumer_usages[out->n_consumer++] = (uint16_t)usages[k];
                    }
                }
                if (tag == 0x8 && !constant && usage_page == 0x01) {
                    for (size_t k = 0; k < n_usages; k++) {
                        axis_t *a = usages[k] == 0x30 ? &out->x[report_id] : usages[k] == 0x38 ? &out->wheel[report_id] : NULL;
                        if (a != NULL) {
                            *a = (axis_t){.lmin = lmin, .lmax = lmax, .size = report_size, .relative = relative, .seen = true};
                        }
                    }
                }
            } else if (tag == 0xA) {
                if (out->depth == 0 && uval == 0x01) {
                    out->app_collections++;
                }
                out->depth++;
                if (out->depth > out->max_depth) {
                    out->max_depth = out->depth;
                }
            } else if (tag == 0xC) {
                out->depth--;
                if (out->depth < 0) {
                    fail(out, "End Collection without Collection");
                    return;
                }
            } else {
                fail(out, "unknown main item");
                return;
            }
            n_usages = 0; /* local items end with every main item */
        } else if (type == 1) { /* global */
            switch (tag) {
            case 0x0:
                usage_page = uval;
                break;
            case 0x1:
                lmin = sval;
                break;
            case 0x2:
                lmax = sval;
                break;
            case 0x7:
                report_size = uval;
                break;
            case 0x8:
                report_id = uval;
                any_id = true;
                out->id_item = true;
                if (report_id == 0 || report_id >= MAX_ID) {
                    fail(out, "bad report id");
                    return;
                }
                if (out->depth != 1) {
                    fail(out, "report id outside a top-level collection");
                    return;
                }
                break;
            case 0x9:
                report_count = uval;
                break;
            default:
                break;
            }
        } else if (type == 2) { /* local */
            if (tag == 0x0 && n_usages < 64) {
                usages[n_usages++] = uval;
            }
        } else {
            fail(out, "reserved item type");
            return;
        }
        i += 1 + size;
    }
    if (out->depth != 0) {
        fail(out, "unbalanced collections");
    }
}

/* Check one built descriptor against the collection table. */
static void check_desc(unsigned mask, bool with_ids)
{
    uint8_t buf[HID_DESC_MAX];
    const size_t len = hid_desc_build(buf, sizeof(buf), mask, with_ids);
    CHECK(len > 0 && len <= HID_DESC_MAX);
    desc_info_t info;
    parse(buf, len, &info);
    if (info.error) {
        fprintf(stderr, "  descriptor error (mask 0x%02X, ids %d): %s\n", mask, with_ids, info.why);
    }
    CHECK(!info.error);
    unsigned n_colls = 0;
    unsigned want_ids = 0;
    for (unsigned c = 0; c < HID_COLL_COUNT; c++) {
        if (mask & HID_COLL_BIT(c)) {
            n_colls++;
            want_ids |= 1u << hid_coll_report_id((hid_coll_t)c);
        }
    }
    CHECK_EQ(info.app_collections, n_colls);
    if (!with_ids) {
        /* only single-collection descriptors are built without ids */
        if (n_colls != 1) {
            return;
        }
        want_ids = 1u; /* everything under "id 0" */
    }
    CHECK_EQ(info.ids_seen, want_ids);
    for (unsigned c = 0; c < HID_COLL_COUNT; c++) {
        if (!(mask & HID_COLL_BIT(c))) {
            continue;
        }
        const unsigned id = with_ids ? hid_coll_report_id((hid_coll_t)c) : 0u;
        CHECK_EQ(info.in_bits[id], hid_coll_input_len((hid_coll_t)c) * 8u);
        CHECK_EQ(info.out_bits[id], c == HID_COLL_KEYBOARD ? 8u : 0u);
        CHECK_EQ(info.feat_bits[id], 0);
        if (c == HID_COLL_MOUSE) {
            CHECK(info.x[id].seen && info.x[id].relative);
            CHECK_EQ(info.x[id].lmin, -127);
            CHECK_EQ(info.x[id].lmax, 127);
            CHECK_EQ(info.x[id].size, 8);
        }
        if (c == HID_COLL_ABS_POINTER) {
            CHECK(info.x[id].seen && !info.x[id].relative);
            CHECK_EQ(info.x[id].lmin, 0);
            CHECK_EQ(info.x[id].lmax, HID_ABS_LOGICAL_MAX);
            CHECK_EQ(info.x[id].size, 16);
            CHECK(info.wheel[id].seen && info.wheel[id].relative);
            CHECK_EQ(info.wheel[id].lmin, -127);
        }
    }
}

TEST(test_every_descriptor_variant)
{
    for (unsigned mask = 1; mask <= HID_COLL_ALL; mask++) {
        check_desc(mask, true);
        check_desc(mask, false);
    }
    uint8_t buf[HID_DESC_MAX];
    CHECK(hid_desc_build(buf, sizeof(buf), HID_COLL_ALL, true) <= HID_DESC_MAX);
}

TEST(test_desc_build_refuses_bad_input)
{
    uint8_t buf[HID_DESC_MAX];
    CHECK_EQ(hid_desc_build(buf, sizeof(buf), 0, true), 0);
    CHECK_EQ(hid_desc_build(buf, sizeof(buf), HID_COLL_ALL + 1u, true), 0);
    CHECK_EQ(hid_desc_build(NULL, sizeof(buf), 1, true), 0);
    const size_t full = hid_desc_build(buf, sizeof(buf), HID_COLL_ALL, true);
    CHECK(full > 0);
    /* One byte short of the space needed: refused, and nothing written past the capacity. */
    uint8_t small[HID_DESC_MAX + 8];
    memset(small, 0xEE, sizeof(small));
    CHECK_EQ(hid_desc_build(small, full - 1, HID_COLL_ALL, true), 0);
    for (size_t i = full - 1; i < sizeof(small); i++) {
        CHECK_EQ(small[i], 0xEE);
    }
    CHECK_EQ(hid_desc_build(small, full, HID_COLL_ALL, true), full);
}

TEST(test_ids_and_lengths)
{
    CHECK_EQ(hid_coll_report_id(HID_COLL_KEYBOARD), 1);
    CHECK_EQ(hid_coll_report_id(HID_COLL_MOUSE), 2);
    CHECK_EQ(hid_coll_report_id(HID_COLL_CONSUMER), 3);
    CHECK_EQ(hid_coll_report_id(HID_COLL_SYSTEM), 4);
    CHECK_EQ(hid_coll_report_id(HID_COLL_ABS_POINTER), 5);
    CHECK_EQ(hid_coll_report_id(HID_COLL_COUNT), 0);
    CHECK_EQ(hid_coll_input_len(HID_COLL_ABS_POINTER), CH9329_ABS_REPORT_LEN);
    CHECK_EQ(hid_coll_input_len(HID_COLL_KEYBOARD), CH9329_KB_REPORT_LEN);
    CHECK_EQ(hid_coll_input_len((hid_coll_t)9), 0);
    CHECK(strcmp(hid_coll_name((hid_coll_t)9), "?") == 0);
}

TEST(test_keyboard_only_has_no_pointer)
{
    const unsigned mask = hid_profile_collections(HID_PROFILE_KEYBOARD, HID_POINTERS_REL_AND_ABS);
    CHECK_EQ(mask, HID_COLL_BIT(HID_COLL_KEYBOARD));
    uint8_t buf[HID_DESC_MAX];
    const size_t len = hid_desc_build(buf, sizeof(buf), mask, true);
    /* No Generic Desktop Mouse (0x02) or Pointer (0x01) usage anywhere in the map. */
    for (size_t i = 2; i + 1 < len; i++) {
        CHECK(!(buf[i] == 0x09 && (buf[i + 1] == 0x02 || buf[i + 1] == 0x01) && buf[i - 2] == 0x05 &&
                buf[i - 1] == 0x01));
    }
}

TEST(test_profiles)
{
    const unsigned K = HID_COLL_BIT(HID_COLL_KEYBOARD), M = HID_COLL_BIT(HID_COLL_MOUSE),
                   C = HID_COLL_BIT(HID_COLL_CONSUMER), S = HID_COLL_BIT(HID_COLL_SYSTEM),
                   A = HID_COLL_BIT(HID_COLL_ABS_POINTER);
    CHECK_EQ(hid_profile_collections(HID_PROFILE_COMPOSITE, HID_POINTERS_REL_AND_ABS), K | M | C | S | A);
    CHECK_EQ(hid_profile_collections(HID_PROFILE_COMPOSITE, HID_POINTERS_REL_ONLY), K | M | C | S);
    CHECK_EQ(hid_profile_collections(HID_PROFILE_COMPOSITE, HID_POINTERS_ABS_ONLY), K | A | C | S);
    CHECK_EQ(hid_profile_collections(HID_PROFILE_MOUSE, HID_POINTERS_REL_AND_ABS), M | A);
    CHECK_EQ(hid_profile_collections(HID_PROFILE_MOUSE, HID_POINTERS_ABS_ONLY), A);
    for (unsigned p = 0; p < HID_PROFILE_COUNT; p++) {
        for (unsigned q = 0; q < HID_POINTERS_COUNT; q++) {
            CHECK(hid_profile_collections((hid_profile_t)p, (hid_pointers_t)q) != 0u);
        }
    }
    CHECK_EQ(ch9329_profile_for_work_mode(0x00), HID_PROFILE_COMPOSITE);
    CHECK_EQ(ch9329_profile_for_work_mode(0x01), HID_PROFILE_KEYBOARD);
    CHECK_EQ(ch9329_profile_for_work_mode(0x02), HID_PROFILE_MOUSE);
    CHECK_EQ(ch9329_profile_for_work_mode(0x03), HID_PROFILE_COMPOSITE);
    CHECK_EQ(ch9329_profile_for_work_mode(0x80), HID_PROFILE_COMPOSITE);
    CHECK(strcmp(hid_profile_name(HID_PROFILE_KEYBOARD), "keyboard-only") == 0);
}

TEST(test_consumer_usages_match_ch9329_table)
{
    /* Independent copy of the CH9329 multimedia table -> HID Consumer usage IDs. */
    static const struct {
        const char *ch9329;
        uint16_t usage;
    } expected[24] = {
        {"Volume+", 0x00E9},     {"Volume-", 0x00EA},       {"Mute", 0x00E2},         {"Play/Pause", 0x00CD},
        {"Next Track", 0x00B5},  {"Prev Track", 0x00B6},    {"CD Stop", 0x00B7},      {"Eject", 0x00B8},
        {"E-Mail", 0x018A},      {"WWW Search", 0x0221},    {"WWW Favorites", 0x022A}, {"WWW Home", 0x0223},
        {"WWW Back", 0x0224},    {"WWW Forward", 0x0225},   {"WWW Stop", 0x0226},     {"Refresh", 0x0227},
        {"Media", 0x0183},       {"Explorer", 0x0196},      {"Calculator", 0x0192},   {"Screen Save", 0x01B1},
        {"My Computer", 0x0194}, {"Minimize", 0x0206},      {"Record", 0x00B2},       {"Rewind", 0x00B4},
    };
    uint8_t buf[HID_DESC_MAX];
    const size_t len = hid_desc_build(buf, sizeof(buf), HID_COLL_BIT(HID_COLL_CONSUMER), true);
    desc_info_t info;
    parse(buf, len, &info);
    CHECK(!info.error);
    CHECK_EQ(info.n_consumer, 24);
    for (size_t i = 0; i < 24 && i < info.n_consumer; i++) {
        if (info.consumer_usages[i] != expected[i].usage) {
            fprintf(stderr, "  bit %zu (%s): descriptor has 0x%03X, expected 0x%03X\n", i, expected[i].ch9329,
                    info.consumer_usages[i], expected[i].usage);
        }
        CHECK_EQ(info.consumer_usages[i], expected[i].usage);
        CHECK_EQ(hid_consumer_usages[i], expected[i].usage);
    }
}

/* ---- BLE Report Map: report ids only with several collections -------------------------------- */

static unsigned n_colls(unsigned mask)
{
    unsigned n = 0;
    for (unsigned c = 0; c < HID_COLL_COUNT; c++) {
        n += (mask & HID_COLL_BIT(c)) != 0u;
    }
    return n;
}

TEST(test_ble_map_report_ids)
{
    for (unsigned mask = 1; mask <= HID_COLL_ALL; mask++) {
        const bool several = n_colls(mask) > 1u;
        CHECK_EQ(hid_map_uses_ids(mask), several);
        uint8_t map[HID_DESC_MAX], ref[HID_DESC_MAX];
        const size_t len = hid_map_build(map, sizeof(map), mask);
        CHECK(len > 0 && len == hid_desc_build(ref, sizeof(ref), mask, several));
        CHECK(memcmp(map, ref, len) == 0);
        desc_info_t info;
        parse(map, len, &info);
        CHECK(!info.error);
        CHECK_EQ(info.id_item, several); /* a single-collection map has no Report ID item at all */
        /* Report Reference ids: the id the map gives each collection's reports; collections
         * outside the map keep their own id, which the map does not use; no two report
         * characteristics (same type) ever share an id. */
        unsigned refs = 0;
        for (unsigned c = 0; c < HID_COLL_COUNT; c++) {
            const uint8_t id = hid_map_report_id(mask, (hid_coll_t)c);
            if (mask & HID_COLL_BIT(c)) {
                CHECK_EQ(id, several ? hid_coll_report_id((hid_coll_t)c) : 0u);
                CHECK(info.ids_seen & (1u << id));
                CHECK_EQ(info.in_bits[id], hid_coll_input_len((hid_coll_t)c) * 8u);
            } else {
                CHECK_EQ(id, hid_coll_report_id((hid_coll_t)c));
                CHECK(!(info.ids_seen & (1u << id)));
            }
            CHECK(!(refs & (1u << id)));
            refs |= 1u << id;
        }
    }
    CHECK_EQ(hid_map_report_id(HID_COLL_ALL, HID_COLL_COUNT), 0);
    CHECK(!hid_map_uses_ids(0));
}

TEST(test_ble_map_ids_per_profile)
{
    /* Keyboard-only and the single-pointer mouse-only builds have one collection: no ids. */
    for (unsigned p = 0; p < HID_PROFILE_COUNT; p++) {
        for (unsigned q = 0; q < HID_POINTERS_COUNT; q++) {
            const unsigned mask = hid_profile_collections((hid_profile_t)p, (hid_pointers_t)q);
            const bool single = p == HID_PROFILE_KEYBOARD || (p == HID_PROFILE_MOUSE && q != HID_POINTERS_REL_AND_ABS);
            CHECK_EQ(hid_map_uses_ids(mask), !single);
        }
    }
}

/* The map `mask` builds, with and without ids, against pinned bytes (without the id). */
static void check_map_bytes(unsigned mask, uint8_t id, const char *hex_without_id)
{
    uint8_t want[HID_DESC_MAX];
    const size_t n = hex_bytes(hex_without_id, want, sizeof(want));
    uint8_t got[HID_DESC_MAX];
    const size_t len = hid_map_build(got, sizeof(got), mask);
    CHECK_EQ(len, n);
    CHECK_MEM(got, want, n < len ? n : len);
    /* With ids (the old BLE map, and what a map with several collections contains): the same
     * bytes with "85 <id>" after the 6-byte Usage Page / Usage / Collection head. */
    const size_t len_id = hid_desc_build(got, sizeof(got), mask, true);
    CHECK_EQ(len_id, n + 2u);
    CHECK_MEM(got, want, 6);
    CHECK_EQ(got[6], 0x85);
    CHECK_EQ(got[7], id);
    CHECK_MEM(&got[8], &want[6], n - 6u);
}

TEST(test_single_collection_map_bytes)
{
    /* What the iPhone reads as the BLE Report Map in the single-collection builds. Pinned: iOS
     * caches it for the life of the bond, so any change must be deliberate (README: Forget and
     * pair again). */
    check_map_bytes(HID_COLL_BIT(HID_COLL_KEYBOARD), 1,
                    "05 01 09 06 A1 01 05 07 19 E0 29 E7 15 00 25 01 75 01 95 08 81 02 95 01 75 08 81 01 "
                    "05 08 19 01 29 05 95 05 75 01 91 02 95 01 75 03 91 01 05 07 19 00 2A FF 00 15 00 "
                    "26 FF 00 95 06 75 08 81 00 C0");
    check_map_bytes(HID_COLL_BIT(HID_COLL_ABS_POINTER), 5,
                    "05 01 09 02 A1 01 09 01 A1 00 05 09 19 01 29 03 15 00 25 01 95 03 75 01 81 02 95 01 "
                    "75 05 81 03 05 01 09 30 09 31 16 00 00 26 FF 7F 75 10 95 02 81 02 09 38 15 81 25 7F "
                    "75 08 95 01 81 06 C0 C0");
    check_map_bytes(HID_COLL_BIT(HID_COLL_MOUSE), 2,
                    "05 01 09 02 A1 01 09 01 A1 00 05 09 19 01 29 03 15 00 25 01 95 03 75 01 81 02 95 01 "
                    "75 05 81 03 05 01 09 30 09 31 09 38 15 81 25 7F 75 08 95 03 81 06 C0 C0");
}

/* ---- idle input values: never a (0,0) absolute report ----------------------------------------- */

TEST(test_idle_reports)
{
    for (unsigned c = 0; c < HID_COLL_COUNT; c++) {
        uint8_t buf[16];
        memset(buf, 0xEE, sizeof(buf));
        const size_t n = hid_coll_idle_report((hid_coll_t)c, buf, sizeof(buf));
        CHECK_EQ(n, hid_coll_input_len((hid_coll_t)c));
        CHECK_EQ(buf[n], 0xEE); /* nothing written past the report */
        if (c == HID_COLL_ABS_POINTER) {
            /* buttons 0, X = Y = 16384 (centre of 0..32767, little-endian), wheel 0 */
            CHECK_MEM(buf, "\x00\x00\x40\x00\x40\x00", 6);
            const unsigned x = buf[1] | (unsigned)buf[2] << 8, y = buf[3] | (unsigned)buf[4] << 8;
            CHECK(x != 0u && y != 0u && x <= HID_ABS_LOGICAL_MAX && y <= HID_ABS_LOGICAL_MAX);
            CHECK_EQ(x, HID_ABS_CENTRE);
            CHECK_EQ(y, HID_ABS_CENTRE);
        } else {
            for (size_t i = 0; i < n; i++) {
                CHECK_EQ(buf[i], 0);
            }
        }
    }
    uint8_t small[8];
    memset(small, 0xEE, sizeof(small));
    CHECK_EQ(hid_coll_idle_report(HID_COLL_ABS_POINTER, small, HID_ABS_REPORT_LEN - 1u), 0);
    CHECK_EQ(small[0], 0xEE);
    CHECK_EQ(hid_coll_idle_report(HID_COLL_COUNT, small, sizeof(small)), 0);
    CHECK_EQ(hid_coll_idle_report(HID_COLL_KEYBOARD, NULL, 8), 0);
    /* The host's grid centre (2048 of 0..4095) lands next to it: the idle value is a real
     * position, not a special value. */
    CHECK(ch9329_abs_scale(2048) - HID_ABS_CENTRE < 8u);
}

/* ---- descriptor identity ---------------------------------------------------------------------- */

TEST(test_identity_tags)
{
    static const char *const WANT[HID_PROFILE_COUNT][HID_POINTERS_COUNT] = {
        [HID_PROFILE_COMPOSITE] = {"RA", "R", "A"},
        [HID_PROFILE_KEYBOARD] = {"K", "K", "K"},
        [HID_PROFILE_MOUSE] = {"MRA", "MR", "MA"},
    };
    for (unsigned p = 0; p < HID_PROFILE_COUNT; p++) {
        for (unsigned q = 0; q < HID_POINTERS_COUNT; q++) {
            const char *tag = hid_identity_tag((hid_profile_t)p, (hid_pointers_t)q);
            CHECK(strcmp(tag, WANT[p][q]) == 0);
            const size_t len = strlen(tag);
            CHECK(len >= 1u && len <= HID_IDENTITY_TAG_MAX);
            for (size_t i = 0; i < len; i++) {
                CHECK(tag[i] >= 'A' && tag[i] <= 'Z'); /* safe in a BLE name (no ':' or ';') and a serial */
            }
            /* Same tag exactly when the phone sees the same collections. */
            for (unsigned p2 = 0; p2 < HID_PROFILE_COUNT; p2++) {
                for (unsigned q2 = 0; q2 < HID_POINTERS_COUNT; q2++) {
                    const bool same_tag = strcmp(tag, hid_identity_tag((hid_profile_t)p2, (hid_pointers_t)q2)) == 0;
                    const bool same_colls = hid_profile_collections((hid_profile_t)p, (hid_pointers_t)q) ==
                                            hid_profile_collections((hid_profile_t)p2, (hid_pointers_t)q2);
                    CHECK_EQ(same_tag, same_colls);
                }
            }
        }
    }
    /* Out-of-range values fall back exactly like hid_profile_collections(). */
    CHECK(strcmp(hid_identity_tag((hid_profile_t)7, (hid_pointers_t)9), "RA") == 0);
    CHECK_EQ(hid_profile_collections((hid_profile_t)7, (hid_pointers_t)9),
             hid_profile_collections(HID_PROFILE_COMPOSITE, HID_POINTERS_REL_AND_ABS));
}

TEST(test_identity_string)
{
    char out[32];
    CHECK_EQ(hid_identity_string(out, sizeof(out), "HID Bridge 1A2B", HID_PROFILE_COMPOSITE, HID_POINTERS_REL_AND_ABS),
             18);
    CHECK(strcmp(out, "HID Bridge 1A2B-RA") == 0);
    hid_identity_string(out, sizeof(out), "A0B1C2D3E4F5", HID_PROFILE_COMPOSITE, HID_POINTERS_ABS_ONLY);
    CHECK(strcmp(out, "A0B1C2D3E4F5-A") == 0);
    hid_identity_string(out, sizeof(out), "A0B1C2D3E4F5", HID_PROFILE_KEYBOARD, HID_POINTERS_ABS_ONLY);
    CHECK(strcmp(out, "A0B1C2D3E4F5-K") == 0);
    /* A product string of the maximum length (23) plus the longest tag fits a BLE name (29). */
    char name[29 + 1];
    CHECK_EQ(hid_identity_string(name, sizeof(name), "ABCDEFGHIJKLMNOPQRSTUVW", HID_PROFILE_MOUSE,
                                 HID_POINTERS_REL_AND_ABS),
             27);
    CHECK(strcmp(name, "ABCDEFGHIJKLMNOPQRSTUVW-MRA") == 0);
    /* Too long: the base is shortened, never the tag. */
    CHECK_EQ(hid_identity_string(out, 10, "ABCDEFGHIJKLMNOP", HID_PROFILE_MOUSE, HID_POINTERS_REL_AND_ABS), 9);
    CHECK(strcmp(out, "ABCDE-MRA") == 0);
    CHECK_EQ(hid_identity_string(out, 5, "ABCDEFGHIJKLMNOP", HID_PROFILE_MOUSE, HID_POINTERS_REL_AND_ABS), 3);
    CHECK(strcmp(out, "MRA") == 0);
    memset(out, 'x', sizeof(out));
    CHECK_EQ(hid_identity_string(out, 4, "AB", HID_PROFILE_MOUSE, HID_POINTERS_REL_AND_ABS), 0);
    CHECK_EQ(out[0], '\0');
    /* No base: the tag alone. */
    CHECK_EQ(hid_identity_string(out, sizeof(out), "", HID_PROFILE_COMPOSITE, HID_POINTERS_REL_ONLY), 1);
    CHECK(strcmp(out, "R") == 0);
    CHECK_EQ(hid_identity_string(out, sizeof(out), NULL, HID_PROFILE_KEYBOARD, HID_POINTERS_REL_ONLY), 1);
    CHECK(strcmp(out, "K") == 0);
    CHECK_EQ(hid_identity_string(NULL, 8, "x", HID_PROFILE_KEYBOARD, HID_POINTERS_REL_ONLY), 0);
    CHECK_EQ(hid_identity_string(out, 0, "x", HID_PROFILE_KEYBOARD, HID_POINTERS_REL_ONLY), 0);
}

TEST(test_parser_self_check)
{
    /* The mini parser itself must reject broken descriptors. */
    desc_info_t info;
    const uint8_t unbalanced[] = {0x05, 0x01, 0x09, 0x06, 0xA1, 0x01};
    parse(unbalanced, sizeof(unbalanced), &info);
    CHECK(info.error);
    const uint8_t truncated[] = {0x05, 0x01, 0x26, 0xFF};
    parse(truncated, sizeof(truncated), &info);
    CHECK(info.error);
    const uint8_t mixed_ids[] = {0xA1, 0x01, 0x85, 0x01, 0x75, 0x08, 0x95, 0x01, 0x81, 0x02, 0xC0,
                                 0xA1, 0x01, 0x85, 0x00, 0xC0};
    parse(mixed_ids, sizeof(mixed_ids), &info);
    CHECK(info.error);
}

void run_hid_desc_tests(void);
void run_hid_desc_tests(void)
{
    RUN(test_every_descriptor_variant);
    RUN(test_desc_build_refuses_bad_input);
    RUN(test_ids_and_lengths);
    RUN(test_keyboard_only_has_no_pointer);
    RUN(test_profiles);
    RUN(test_consumer_usages_match_ch9329_table);
    RUN(test_ble_map_report_ids);
    RUN(test_ble_map_ids_per_profile);
    RUN(test_single_collection_map_bytes);
    RUN(test_idle_reports);
    RUN(test_identity_tags);
    RUN(test_identity_string);
    RUN(test_parser_self_check);
}
