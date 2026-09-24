#include "tinytest.h"

void run_proto_tests(void);
void run_hid_desc_tests(void);

int main(void)
{
    run_proto_tests();
    run_hid_desc_tests();
    return tt_summary();
}
