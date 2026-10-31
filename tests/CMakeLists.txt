include(GoogleTest)

add_executable(unit_tests
    mocks/ble_mock.cpp
    test_protocol.cpp
    test_ble_mock_example.cpp
)

target_link_libraries(unit_tests PRIVATE
    application_core
    GTest::gtest
    GTest::gtest_main
    GTest::gmock
)

target_include_directories(unit_tests PRIVATE
    ${CMAKE_SOURCE_DIR}/src
    ${CMAKE_CURRENT_SOURCE_DIR}
)

gtest_discover_tests(unit_tests)
