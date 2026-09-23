from __future__ import annotations

import argparse
import os
from pathlib import Path
import secrets
from urllib.parse import quote


def _is_local_host(host: str) -> bool:
    normalized = host.strip().lower()
    return (
        normalized.startswith("http://localhost")
        or normalized.startswith("http://127.0.0.1")
        or normalized.startswith("http://host.docker.internal")
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate an EventForge production .env file without printing secrets."
    )
    parser.add_argument(
        "--host",
        default="http://localhost",
        help="Caddy site address. Use http://localhost for local staging or a DNS name for public TLS.",
    )
    parser.add_argument("--output", default=".env.production")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    output = Path(args.output).resolve()
    if output.exists() and not args.force:
        raise SystemExit(f"{output} already exists; pass --force to replace it")

    host = args.host.strip()
    if not host:
        raise SystemExit("--host cannot be blank")

    db_password = secrets.token_urlsafe(32)
    admin_password = secrets.token_urlsafe(36)
    webhook_master_secret = secrets.token_urlsafe(48)

    db_name = "eventforge"
    db_user = "eventforge"
    database_url = (
        "postgresql://"
        + quote(db_user, safe="")
        + ":"
        + quote(db_password, safe="")
        + "@db:5432/"
        + quote(db_name, safe="")
    )

    if _is_local_host(host):
        http_bind = "8080"
        https_bind = "8443"
    else:
        http_bind = "80"
        https_bind = "443"

    values = {
        "POSTGRES_DB": db_name,
        "POSTGRES_USER": db_user,
        "POSTGRES_PASSWORD": db_password,
        "DATABASE_URL": database_url,
        "EVENTFORGE_PUBLIC_HOST": host,
        "EVENTFORGE_HTTP_BIND": http_bind,
        "EVENTFORGE_HTTPS_BIND": https_bind,
        "EVENTFORGE_ENV": "production",
        "EVENTFORGE_ADMIN_USER": "eventforge",
        "EVENTFORGE_ADMIN_PASSWORD": admin_password,
        "EVENTFORGE_WEBHOOK_MASTER_SECRET": webhook_master_secret,
        "EVENTFORGE_REQUIRE_SIGNED_WEBHOOKS": "1",
        "EVENTFORGE_MAX_INGEST_BYTES": "1048576",
        "EVENTFORGE_WORKER_POLL_SECONDS": "0.5",
        "EVENTFORGE_JOB_LEASE_SECONDS": "30",
        "EVENTFORGE_JOB_HEARTBEAT_SECONDS": "10",
        "EVENTFORGE_RETRY_BASE_SECONDS": "2",
        "EVENTFORGE_RETRY_MAX_SECONDS": "600",
        "EVENTFORGE_HTTP_ACTION_MAX_RESPONSE_BYTES": "1048576",
        "EVENTFORGE_ALLOW_PRIVATE_HTTP_ACTIONS": "0",
        "EVENTFORGE_ALLOW_INSECURE_HTTP_ACTIONS": "0",
        "EVENTFORGE_HTTP_ACTION_ALLOWED_PORTS": "443",
        "EVENTFORGE_BACKUP_DIR": "./backups",
    }

    output.write_text(
        "\n".join(f"{key}={value}" for key, value in values.items()) + "\n",
        encoding="utf-8",
    )
    try:
        os.chmod(output, 0o600)
    except OSError:
        pass

    print(f"wrote {output}")
    print("production secrets were generated and were not printed")
    print(f"public_host={host}")
    print(f"http_bind={http_bind}")
    print(f"https_bind={https_bind}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
