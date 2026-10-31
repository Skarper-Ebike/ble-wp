#include <gtest/gtest.h>

extern "C" {
#include "application/protocol.h"
}

TEST(Crc16, StandardCheckValue)
{
    const uint8_t msg[] = { '1', '2', '3', '4', '5', '6', '7', '8', '9' };
    EXPECT_EQ(crc16_ccitt(msg, sizeof(msg)), 0x29B1);
}
