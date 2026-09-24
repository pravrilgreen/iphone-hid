/*
 * Flash storage for the bridge: the CH9329 parameter block + strings (what SET_PARA_CFG /
 * SET_USB_STRING / SET_DEFAULT_CFG write), and small markers used by the BLE layer.
 *
 * All functions block on flash: call them from the bridge task or app_main, never from the
 * NimBLE host task or a GATT/GAP callback.
 */
#pragma once

#include <stdbool.h>
#include <stdint.h>

#include "ch9329_proto.h"

/* nvs_flash_init(), erasing the partition if its layout is from another IDF version. */
void persist_init(void);

/* Load the parameter block. false = nothing stored or stored by an incompatible firmware. */
bool persist_load(ch9329_persist_t *out);

/* Store the parameter block (written and committed before returning). */
bool persist_store(const ch9329_persist_t *p);

/* Small unsigned markers (namespace "bridge"). */
bool persist_get_u8(const char *key, uint8_t *out);
bool persist_set_u8(const char *key, uint8_t value);
