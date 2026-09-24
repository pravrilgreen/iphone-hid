# Portable CH9329 protocol core: no ESP-IDF dependency (also built by test_host/Makefile).
idf_component_register(SRCS "src/ch9329_parser.c" "src/ch9329_core.c" "src/hid_report_map.c"
                       INCLUDE_DIRS "include")

# Same strictness as the host build (test_host/Makefile): this code runs on the device.
target_compile_options(${COMPONENT_LIB} PRIVATE -Wall -Wextra -Werror -Wshadow -Wconversion -Wsign-conversion
                       -Wstrict-prototypes -Wmissing-prototypes)
