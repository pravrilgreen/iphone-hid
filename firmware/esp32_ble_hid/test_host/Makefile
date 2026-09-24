# Host unit tests for the portable CH9329 core (components/ch9329_proto).
#
#   make            build and run the unit tests (ASan + UBSan)
#   make sim        build the pty simulator (the core behind a pseudo-terminal)
#   make e2e        run the repo's Python CH9329 driver (ihc/hid/ch9329.py) against the simulator
#   make check      test + e2e
#   make clean
#
# No dependency beyond gcc (or clang: make CC=clang) and GNU make; `e2e` also needs the repo's
# Python environment (pyserial), see README.md.

# gcc by default (make's built-in default is `cc`); `make CC=clang` works too.
ifeq ($(origin CC),default)
CC := gcc
endif
PROTO := ../components/ch9329_proto
BUILD := build

WARN := -Wall -Wextra -Werror -Wshadow -Wstrict-prototypes -Wmissing-prototypes -Wpedantic \
        -Wformat=2 -Wundef -Wcast-align -Wpointer-arith
# The portable core is also held to -Wconversion: it is the code that runs on the device.
CORE_WARN := $(WARN) -Wconversion -Wsign-conversion
SAN := -fsanitize=address,undefined -fno-sanitize-recover=all -fno-omit-frame-pointer
CFLAGS_COMMON := -std=c11 -D_DEFAULT_SOURCE -O1 -g -I$(PROTO)/include

CORE_SRC := $(PROTO)/src/ch9329_parser.c $(PROTO)/src/ch9329_core.c $(PROTO)/src/hid_report_map.c
TEST_SRC := tinytest.c fake_sink.c test_proto.c test_hid_desc.c test_main.c

CORE_OBJ := $(patsubst $(PROTO)/src/%.c,$(BUILD)/core_%.o,$(CORE_SRC))
TEST_OBJ := $(patsubst %.c,$(BUILD)/%.o,$(TEST_SRC))

.PHONY: all test sim e2e check clean
all: test

$(BUILD):
	mkdir -p $(BUILD)

$(BUILD)/core_%.o: $(PROTO)/src/%.c $(wildcard $(PROTO)/include/*.h) | $(BUILD)
	$(CC) $(CFLAGS_COMMON) $(CORE_WARN) $(SAN) -c $< -o $@

$(BUILD)/%.o: %.c $(wildcard *.h) $(wildcard $(PROTO)/include/*.h) | $(BUILD)
	$(CC) $(CFLAGS_COMMON) $(WARN) $(SAN) -c $< -o $@

$(BUILD)/run_tests: $(CORE_OBJ) $(TEST_OBJ)
	$(CC) $(SAN) $^ -o $@

test: $(BUILD)/run_tests
	./$(BUILD)/run_tests

# The simulator is a long-running process: same warnings and sanitizers as the tests.
$(BUILD)/sim_bridge: sim_bridge.c $(CORE_SRC) $(wildcard $(PROTO)/include/*.h) | $(BUILD)
	$(CC) $(CFLAGS_COMMON) $(WARN) -fsanitize=address,undefined -fno-sanitize-recover=all \
	    sim_bridge.c $(CORE_SRC) -o $@

sim: $(BUILD)/sim_bridge

# Uses the repository's virtualenv (/home/user/iphone-hid/.venv) if present, else python3.
PYTHON ?= $(firstword $(wildcard ../../../.venv/bin/python) python3)
e2e: $(BUILD)/sim_bridge
	$(PYTHON) e2e_host_driver.py ./$(BUILD)/sim_bridge

# Everything that runs without hardware.
check: test e2e

clean:
	rm -rf $(BUILD)
