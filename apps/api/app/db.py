import os
from typing import Any

import psycopg
from psycopg.rows import dict_row

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://eventforge:eventforge_dev_only@localhost:5432/eventforge",
)


def db_connect() -> psycopg.Connection[Any]:
    return psycopg.connect(DATABASE_URL, row_factory=dict_row)
