add_executable(eventforge_gateway
    src/main.cpp
)

target_link_libraries(eventforge_gateway PRIVATE eventlog)

add_test(NAME gateway_smoke_test COMMAND eventforge_gateway --self-test)
