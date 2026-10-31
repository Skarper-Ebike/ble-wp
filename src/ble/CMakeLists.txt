# UUID helpers: no RTOS, used by firmware and tests
add_library(ble_util STATIC ble_uuid.c)
target_include_directories(ble_util PUBLIC ${CMAKE_SOURCE_DIR}/src)

# Simulated BLE stack: firmware only
if(BUILD_APP)
    add_library(ble_sim STATIC ble_central_sim.c)
    target_link_libraries(ble_sim PUBLIC ble_util freertos_kernel)
endif()
