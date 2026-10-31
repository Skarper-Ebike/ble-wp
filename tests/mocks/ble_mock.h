#pragma once

/* GMock stand-in for the ble_central.h API. Create a BleMock in a test and
 * every ble_* call made by the code under test is routed to it.
 * ble_uuid16 / ble_uuid_eq are real (from ble_util). */

#include <gmock/gmock.h>

extern "C" {
#include "ble/ble_central.h"
}

class BleMock {
public:
    BleMock();
    ~BleMock();

    MOCK_METHOD(ble_status_t, ble_init, (ble_evt_handler_t));
    MOCK_METHOD(ble_status_t, ble_scan_start, ());
    MOCK_METHOD(ble_status_t, ble_scan_stop, ());
    MOCK_METHOD(ble_status_t, ble_connect, (const ble_addr_t *));
    MOCK_METHOD(ble_status_t, ble_disconnect, (ble_conn_t));
    MOCK_METHOD(ble_status_t, ble_gattc_discover, (ble_conn_t));
    MOCK_METHOD(ble_status_t, ble_gattc_read, (ble_conn_t, uint16_t));
    MOCK_METHOD(ble_status_t, ble_gattc_write, (ble_conn_t, uint16_t, const void *, uint16_t, bool));
    MOCK_METHOD(ble_status_t, ble_gattc_subscribe, (ble_conn_t, uint16_t, bool));
    MOCK_METHOD(ble_status_t, ble_mtu_request, (ble_conn_t, uint16_t));
};
