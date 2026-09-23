#include <stdio.h>
#include <string.h>

#include "eventforge/eventlog.h"

int main(void) {
    const char *version = ev_eventlog_version();

    if (strcmp(version, "0.0.0") != 0) {
        fprintf(stderr, "unexpected version: %s\n", version);
        return 1;
    }

    printf("eventlog smoke test passed\n");
    return 0;
}
