#include <iostream>
#include <string_view>

#include "eventforge/eventlog.h"

int main(int argc, char **argv) {
    if (argc == 2 && std::string_view(argv[1]) == "--self-test") {
        std::cout << "gateway self-test ok; eventlog=" << ev_eventlog_version() << "\n";
        return 0;
    }

    std::cout << "EventForge C++ gateway placeholder v0.0.0\n";
    std::cout << "Networking begins in the QuotaGate milestone.\n";
    return 0;
}
