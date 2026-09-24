# Portable CH9329 protocol core: no ESP-IDF dependency (also built by test_host/Makefile).
idf_component_register(SRCS "src/ch9329_parser.c" "src/ch9329_core.c" "src/hid_report_map.c"
                            "src/hid_confirm.c"
                       INCLUDE_DIRS "include")

# Same strictness as the host build (test_host/Makefile): this code runs on the device.
# (IDF adds -Wno-sign-compare -Wno-unused-parameter globally; they are switched back on here.)
target_compile_options(${COMPONENT_LIB} PRIVATE -Wall -Wextra -Werror -Wshadow -Wconversion -Wsign-conversion
                       -Wsign-compare -Wunused-parameter -Wstrict-prototypes -Wmissing-prototypes)
