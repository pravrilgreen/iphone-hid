#include "tinytest.h"

int tt_checks;
int tt_failures;
int tt_tests;
int tt_failed_tests;
const char *tt_current = "?";
static int failures_at_start;

void tt_fail(const char *file, int line, const char *msg)
{
    tt_failures++;
    fprintf(stderr, "  FAIL %s (%s:%d): %s\n", tt_current, file, line, msg);
}

void tt_fail_eq(const char *file, int line, const char *expr, long long a, long long b)
{
    tt_failures++;
    fprintf(stderr, "  FAIL %s (%s:%d): %s  [%lld (0x%llx) != %lld (0x%llx)]\n", tt_current, file, line, expr, a,
            (unsigned long long)a, b, (unsigned long long)b);
}

static void dump(const char *label, const uint8_t *p, size_t n)
{
    fprintf(stderr, "       %s:", label);
    for (size_t i = 0; i < n; i++) {
        fprintf(stderr, " %02X", p[i]);
    }
    fprintf(stderr, "\n");
}

void tt_fail_mem(const char *file, int line, const char *expr, const uint8_t *a, const uint8_t *b, size_t n)
{
    tt_failures++;
    fprintf(stderr, "  FAIL %s (%s:%d): %s\n", tt_current, file, line, expr);
    dump("got     ", a, n);
    dump("expected", b, n);
}

void tt_run(const char *name, void (*fn)(void))
{
    tt_current = name;
    failures_at_start = tt_failures;
    tt_tests++;
    fn();
    if (tt_failures != failures_at_start) {
        tt_failed_tests++;
        fprintf(stderr, "FAILED %s\n", name);
    }
}

int tt_summary(void)
{
    printf("%d tests, %d checks, %d failed checks in %d tests\n", tt_tests, tt_checks, tt_failures, tt_failed_tests);
    if (tt_failures == 0) {
        printf("ALL TESTS PASSED\n");
    }
    return tt_failures == 0 ? 0 : 1;
}
