from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from typing import Sequence


children: list[subprocess.Popen[bytes]] = []
stopping = False


def terminate_children() -> None:
    global stopping
    stopping = True

    for child in children:
        if child.poll() is None:
            child.terminate()

    deadline = time.monotonic() + 10.0
    for child in children:
        if child.poll() is not None:
            continue
        remaining = max(0.0, deadline - time.monotonic())
        try:
            child.wait(timeout=remaining)
        except subprocess.TimeoutExpired:
            child.kill()

    for child in children:
        if child.poll() is None:
            child.wait(timeout=2)


def handle_signal(signum: int, _frame: object) -> None:
    print(f"eventforge_render_signal={signum}", flush=True)
    terminate_children()


def spawn(command: Sequence[str]) -> subprocess.Popen[bytes]:
    print("eventforge_render_start=" + " ".join(command), flush=True)
    child = subprocess.Popen(list(command))
    children.append(child)
    return child


def main() -> int:
    migration = subprocess.run(
        ["/bin/sh", "/app/render_migrate.sh"],
        check=False,
    )
    if migration.returncode != 0:
        print(
            f"eventforge_render_migration_failed={migration.returncode}",
            file=sys.stderr,
            flush=True,
        )
        return migration.returncode or 1

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    worker = spawn([sys.executable, "-m", "app.worker"])
    port = os.getenv("PORT", "10000")
    api = spawn(
        [
            "uvicorn",
            "app.main:app",
            "--host",
            "0.0.0.0",
            "--port",
            port,
            "--no-access-log",
            "--proxy-headers",
            "--forwarded-allow-ips=*",
        ]
    )

    while not stopping:
        worker_status = worker.poll()
        api_status = api.poll()

        if worker_status is not None:
            print(
                f"eventforge_render_worker_exited={worker_status}",
                file=sys.stderr,
                flush=True,
            )
            terminate_children()
            return worker_status if worker_status != 0 else 1

        if api_status is not None:
            print(
                f"eventforge_render_api_exited={api_status}",
                file=sys.stderr,
                flush=True,
            )
            terminate_children()
            return api_status if api_status != 0 else 1

        time.sleep(1.0)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
