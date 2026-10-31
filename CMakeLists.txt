cmake_minimum_required(VERSION 3.22)
project(ble_wp C CXX)

set(CMAKE_C_STANDARD 11)
set(CMAKE_C_STANDARD_REQUIRED ON)
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_EXPORT_COMPILE_COMMANDS ON)

if(NOT CMAKE_BUILD_TYPE)
    set(CMAKE_BUILD_TYPE Debug CACHE STRING "" FORCE)
endif()

add_compile_options(-Wall -Wextra -Wpedantic)

option(BUILD_TESTING "Build unit tests with GoogleTest/GMock" OFF)
option(BUILD_APP "Build the FreeRTOS firmware binary" ON)

include(FetchContent)

if(BUILD_APP)
    set(FREERTOS_PORT GCC_POSIX CACHE STRING "" FORCE)
    set(FREERTOS_HEAP 4 CACHE STRING "" FORCE)

    add_library(freertos_config INTERFACE)
    target_include_directories(freertos_config SYSTEM INTERFACE
        ${CMAKE_CURRENT_SOURCE_DIR}/cmake)

    FetchContent_Declare(
        freertos_kernel
        GIT_REPOSITORY https://github.com/FreeRTOS/FreeRTOS-Kernel.git
        GIT_TAG V11.1.0
        GIT_SHALLOW TRUE
    )
    FetchContent_MakeAvailable(freertos_kernel)
endif()

add_subdirectory(src/ble)
add_subdirectory(src/application)

if(BUILD_APP)
    # FreeRTOS glue (tasks, queues, the BLE event handler) goes here.
    # Logic you want to unit test goes in application_core.
    add_executable(ble_wp
        src/main.c
        src/application/app.c
    )
    target_include_directories(ble_wp PRIVATE ${CMAKE_SOURCE_DIR}/src)
    target_link_libraries(ble_wp PRIVATE
        application_core
        ble_sim
        freertos_kernel
        pthread
    )
endif()

if(BUILD_TESTING)
    enable_testing()
    FetchContent_Declare(
        googletest
        GIT_REPOSITORY https://github.com/google/googletest.git
        GIT_TAG v1.14.0
        GIT_SHALLOW TRUE
    )
    set(gtest_force_shared_crt ON CACHE BOOL "" FORCE)
    FetchContent_MakeAvailable(googletest)
    add_subdirectory(tests)
endif()
