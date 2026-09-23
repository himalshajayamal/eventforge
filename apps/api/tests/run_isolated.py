from __future__ import annotations

import os
from pathlib import Path
import re
import subprocess
import sys

import psycopg

ADMIN_DATABASE_URL = os.getenv(
    "EVENTFORGE_TEST_ADMIN_DATABASE_URL",
    "postgresql://eventforge:eventforge_dev_only@db:5432/postgres",
)
TEST_DATABASE_URL = os.getenv(
    "EVENTFORGE_TEST_DATABASE_URL",
    "postgresql://eventforge:eventforge_dev_only@db:5432/eventforge_test",
)
TEST_DATABASE_NAME = os.getenv("EVENTFORGE_TEST_DATABASE_NAME", "eventforge_test")
MIGRATIONS_DIR = Path(os.getenv("EVENTFORGE_TEST_MIGRATIONS_DIR", "/migrations"))


def _validated_database_name() -> str:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", TEST_DATABASE_NAME):
        raise RuntimeError("EVENTFORGE_TEST_DATABASE_NAME contains unsupported characters")
    return TEST_DATABASE_NAME


def recreate_test_database() -> None:
    database_name = _validated_database_name()
    with psycopg.connect(ADMIN_DATABASE_URL, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT pg_terminate_backend(pid)
                FROM pg_stat_activity
                WHERE datname = %s
                  AND pid <> pg_backend_pid()
                """,
                (database_name,),
            )
            cur.execute(f'DROP DATABASE IF EXISTS "{database_name}"')
            cur.execute(f'CREATE DATABASE "{database_name}"')


def apply_migrations() -> None:
    migrations = sorted(MIGRATIONS_DIR.glob("[0-9][0-9][0-9][0-9]_*.sql"))
    if not migrations:
        raise RuntimeError(f"no migrations found under {MIGRATIONS_DIR}")

    with psycopg.connect(TEST_DATABASE_URL, autocommit=True) as conn:
        with conn.cursor() as cur:
            for migration in migrations:
                sql = migration.read_text(encoding="utf-8")
                cur.execute(sql)
                print(f"applied {migration.name}", flush=True)


def run_pytest() -> int:
    env = os.environ.copy()
    env["DATABASE_URL"] = TEST_DATABASE_URL
    completed = subprocess.run(
        [sys.executable, "-m", "pytest", "-q"],
        env=env,
        check=False,
    )
    return completed.returncode


def main() -> int:
    recreate_test_database()
    apply_migrations()
    return run_pytest()


if __name__ == "__main__":
    raise SystemExit(main())
