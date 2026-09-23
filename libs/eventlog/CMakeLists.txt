add_library(eventlog STATIC
    src/eventlog.c
)

target_include_directories(eventlog PUBLIC include)

add_executable(eventlog_smoke_test
    tests/test_eventlog.c
)

target_link_libraries(eventlog_smoke_test PRIVATE eventlog)
add_test(NAME eventlog_smoke_test COMMAND eventlog_smoke_test)
