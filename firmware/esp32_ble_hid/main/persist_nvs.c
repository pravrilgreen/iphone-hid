/*
 * NVS storage for the CH9329 parameter block. The blob carries a magic, a layout version and its
 * size, so a firmware with a different ch9329_persist_t layout never reinterprets old bytes: it
 * falls back to defaults instead (the core validates the content again on load).
 */
#include "persist_nvs.h"

#include <string.h>

#include "esp_log.h"
#include "nvs.h"
#include "nvs_flash.h"

static const char *TAG = "persist";

#define NS_CFG "ch9329"
#define KEY_CFG "cfg"
#define NS_BRIDGE "bridge"

#define BLOB_MAGIC 0x39333243u /* "C239" little-endian: CH9329 block */
#define BLOB_VERSION 1u

typedef struct {
    uint32_t magic;
    uint16_t version;
    uint16_t size;
    ch9329_persist_t data;
} blob_t;

void persist_init(void)
{
    esp_err_t err = nvs_flash_init();
    if (err == ESP_ERR_NVS_NO_FREE_PAGES || err == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        /* Layout from another IDF version: the only way forward is a clean partition (this also
         * drops BLE bonds; the iPhone must then forget the bridge and pair again). */
        ESP_LOGW(TAG, "NVS partition unusable (%s): erasing", esp_err_to_name(err));
        ESP_ERROR_CHECK(nvs_flash_erase());
        err = nvs_flash_init();
    }
    ESP_ERROR_CHECK(err);
}

bool persist_load(ch9329_persist_t *out)
{
    nvs_handle_t h;
    if (nvs_open(NS_CFG, NVS_READONLY, &h) != ESP_OK) {
        return false; /* namespace does not exist yet: first boot */
    }
    blob_t blob;
    size_t len = sizeof(blob);
    const esp_err_t err = nvs_get_blob(h, KEY_CFG, &blob, &len);
    nvs_close(h);
    if (err != ESP_OK) {
        if (err != ESP_ERR_NVS_NOT_FOUND) {
            ESP_LOGW(TAG, "reading the parameter block failed: %s", esp_err_to_name(err));
        }
        return false;
    }
    if (len != sizeof(blob) || blob.magic != BLOB_MAGIC || blob.version != BLOB_VERSION ||
        blob.size != sizeof(ch9329_persist_t)) {
        ESP_LOGW(TAG, "stored parameter block has another layout (len %u, version %u): using defaults",
                 (unsigned)len, (unsigned)blob.version);
        return false;
    }
    *out = blob.data;
    return true;
}

bool persist_store(const ch9329_persist_t *p)
{
    blob_t blob;
    memset(&blob, 0, sizeof(blob));
    blob.magic = BLOB_MAGIC;
    blob.version = BLOB_VERSION;
    blob.size = (uint16_t)sizeof(ch9329_persist_t);
    blob.data = *p;

    nvs_handle_t h;
    esp_err_t err = nvs_open(NS_CFG, NVS_READWRITE, &h);
    if (err == ESP_OK) {
        err = nvs_set_blob(h, KEY_CFG, &blob, sizeof(blob));
        if (err == ESP_OK) {
            err = nvs_commit(h);
        }
        nvs_close(h);
    }
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "storing the parameter block failed: %s", esp_err_to_name(err));
        return false;
    }
    return true;
}

bool persist_get_u8(const char *key, uint8_t *out)
{
    nvs_handle_t h;
    if (nvs_open(NS_BRIDGE, NVS_READONLY, &h) != ESP_OK) {
        return false;
    }
    const esp_err_t err = nvs_get_u8(h, key, out);
    nvs_close(h);
    return err == ESP_OK;
}

bool persist_set_u8(const char *key, uint8_t value)
{
    nvs_handle_t h;
    esp_err_t err = nvs_open(NS_BRIDGE, NVS_READWRITE, &h);
    if (err == ESP_OK) {
        err = nvs_set_u8(h, key, value);
        if (err == ESP_OK) {
            err = nvs_commit(h);
        }
        nvs_close(h);
    }
    if (err != ESP_OK) {
        ESP_LOGW(TAG, "storing %s failed: %s", key, esp_err_to_name(err));
        return false;
    }
    return true;
}
