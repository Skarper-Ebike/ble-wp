/* How to use the BLE mock. Delete this once you have real tests. */

#include <gtest/gtest.h>

#include "mocks/ble_mock.h"

using ::testing::_;
using ::testing::Return;

TEST(BleMockExample, CodeUnderTestWritesACommand)
{
    BleMock ble;
    EXPECT_CALL(ble, ble_gattc_write(1, 0x0010, _, 3, true)).WillOnce(Return(BLE_OK));

    /* In a real test this call would come from your code. */
    const uint8_t req[] = { 0x01, 0x01, 0x00 };
    EXPECT_EQ(ble_gattc_write(1, 0x0010, req, sizeof(req), true), BLE_OK);
}

TEST(BleMockExample, UuidHelpersAreReal)
{
    ble_uuid_t battery = ble_uuid16(0x180F);
    EXPECT_EQ(battery.b[12], 0x0F);
    EXPECT_EQ(battery.b[13], 0x18);
}
