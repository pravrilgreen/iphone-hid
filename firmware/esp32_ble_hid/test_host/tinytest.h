/*
 * Minimal unit-test framework: no dependencies, no allocation.
 *
 *   TEST(name) { CHECK(x); CHECK_EQ(a, b); CHECK_MEM(p, q, n); }
 *   ... in main: RUN(name); return tt_summary();
 */
#ifndef TINYTEST_H
#define TINYTEST_H

#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

extern int tt_checks;
extern int tt_failures;
extern int tt_tests;
extern int tt_failed_tests;
extern const char *tt_current;

void tt_fail(const char *file, int line, const char *msg);
void tt_fail_eq(const char *file, int line, const char *expr, long long a, long long b);
void tt_fail_mem(const char *file, int line, const char *expr, const uint8_t *a, const uint8_t *b, size_t n);
void tt_run(const char *name, void (*fn)(void));
int tt_summary(void);

#define TEST(name) static void name(void)
#define RUN(name) tt_run(#name, name)

#define CHECK(cond)                                                                                   \
    do {                                                                                              \
        tt_checks++;                                                                                  \
        if (!(cond)) {                                                                                \
            tt_fail(__FILE__, __LINE__, #cond);                                                       \
        }                                                                                             \
    } while (0)

#define CHECK_EQ(a, b)                                                                                \
    do {                                                                                              \
        long long tt_a_ = (long long)(a), tt_b_ = (long long)(b);                                     \
        tt_checks++;                                                                                  \
        if (tt_a_ != tt_b_) {                                                                         \
            tt_fail_eq(__FILE__, __LINE__, #a " == " #b, tt_a_, tt_b_);                               \
        }                                                                                             \
    } while (0)

#define CHECK_MEM(a, b, n)                                                                            \
    do {                                                                                              \
        tt_checks++;                                                                                  \
        if (memcmp((a), (b), (n)) != 0) {                                                             \
            tt_fail_mem(__FILE__, __LINE__, #a " == " #b, (const uint8_t *)(a), (const uint8_t *)(b), \
                        (n));                                                                         \
        }                                                                                             \
    } while (0)

#endif /* TINYTEST_H */
