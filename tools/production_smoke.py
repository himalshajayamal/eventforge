from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request


EXPECTED_VERSION = "0.9.0"
EXPECTED_STATUS = "release-candidate"


def request(
    base_url: str,
    path: str,
    *,
    host_header: str | None = None,
) -> tuple[int, bytes, object]:
    headers = {}
    if host_header:
        headers["Host"] = host_header
    req = urllib.request.Request(
        base_url.rstrip("/") + path,
        method="GET",
        headers=headers,
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            return response.status, response.read(), response.headers
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(), exc.headers


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke-test an EventForge v0.9 production edge.")
    parser.add_argument("--base-url", default="http://localhost:8080")
    parser.add_argument(
        "--host-header",
        default=None,
        help="Override the HTTP Host header. Useful when a Docker test container reaches local Caddy through host.docker.internal.",
    )
    args = parser.parse_args()

    base = args.base_url.rstrip("/")
    parsed = urllib.parse.urlparse(base)
    host_header = args.host_header

    if host_header is None and parsed.hostname == "host.docker.internal":
        # Local production-mode Caddy is configured for http://localhost, while
        # a disposable Docker smoke-test container must reach the Windows host
        # through host.docker.internal. Keep the routing host as localhost.
        host_header = "localhost"

    results: dict[str, object] = {"base_url": base}
    if host_header:
        results["host_header"] = host_header

    status, body, headers = request(base, "/", host_header=host_header)
    require(status == 200, f"frontend returned HTTP {status}")
    content_type = headers.get_content_type()
    require(
        content_type == "text/html" or b"<html" in body[:4096].lower(),
        f"frontend is not HTML (content-type={content_type})",
    )
    results["frontend"] = "ok"

    status, body, _ = request(base, "/api/", host_header=host_header)
    require(status == 200, f"API identity returned HTTP {status}")
    identity = json.loads(body)
    require(identity.get("name") == "EventForge", "unexpected API name")
    require(identity.get("version") == EXPECTED_VERSION, f"unexpected version: {identity.get('version')}")
    require(identity.get("status") == EXPECTED_STATUS, f"unexpected status: {identity.get('status')}")
    results["api_identity"] = identity

    status, body, _ = request(base, "/ready", host_header=host_header)
    require(status == 200, f"readiness returned HTTP {status}")
    ready = json.loads(body)
    require(ready == {"status": "ready", "database": "ok"}, f"unexpected readiness: {ready}")
    results["readiness"] = ready

    status, _, headers = request(base, "/api/projects", host_header=host_header)
    require(status == 401, f"unauthenticated control route returned HTTP {status}, expected 401")
    require(headers.get("WWW-Authenticate") is not None, "401 response is missing WWW-Authenticate")
    results["unauthenticated_control_plane"] = "401"

    print(json.dumps(results, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"production_smoke_failed={exc}", file=sys.stderr)
        raise SystemExit(1)
