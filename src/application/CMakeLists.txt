# Host-testable application code: no FreeRTOS. Talks to the stack only
# through ble_central.h, which is mocked in tests.
add_library(application_core STATIC
    protocol.c
)
target_include_directories(application_core PUBLIC ${CMAKE_SOURCE_DIR}/src)
target_link_libraries(application_core PUBLIC ble_util)
