#include "mocks/ble_mock.h"

#include <cassert>

static BleMock * g_mock = nullptr;

BleMock::BleMock() { assert(!g_mock); g_mock = this; }
BleMock::~BleMock() { g_mock = nullptr; }

static BleMock & mock()
{
    assert(g_mock && "create a BleMock in your test");
    return *g_mock;
}

extern "C" {

ble_status_t ble_init(ble_evt_handler_t h) { return mock().ble_init(h); }
ble_status_t ble_scan_start(void) { return mock().ble_scan_start(); }
ble_status_t ble_scan_stop(void) { return mock().ble_scan_stop(); }
ble_status_t ble_connect(const ble_addr_t * a) { return mock().ble_connect(a); }
ble_status_t ble_disconnect(ble_conn_t c) { return mock().ble_disconnect(c); }
ble_status_t ble_gattc_discover(ble_conn_t c) { return mock().ble_gattc_discover(c); }
ble_status_t ble_gattc_read(ble_conn_t c, uint16_t h) { return mock().ble_gattc_read(c, h); }
ble_status_t ble_gattc_write(ble_conn_t c, uint16_t h, const void * d, uint16_t l, bool r)
{
    return mock().ble_gattc_write(c, h, d, l, r);
}
ble_status_t ble_gattc_subscribe(ble_conn_t c, uint16_t h, bool e) { return mock().ble_gattc_subscribe(c, h, e); }
ble_status_t ble_mtu_request(ble_conn_t c, uint16_t m) { return mock().ble_mtu_request(c, m); }

}
