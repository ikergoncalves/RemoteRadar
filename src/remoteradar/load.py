"""Loading of raw API payloads into the PostgreSQL warehouse (schema ``raw``)."""

from __future__ import annotations

from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from remoteradar.config import database_url

_INSERT_RAW_REMOTIVE = """
    INSERT INTO raw.remotive_jobs (payload)
    VALUES (%s)
    RETURNING id
"""


def insert_raw_remotive_payload(payload: dict[str, Any], *, dsn: str | None = None) -> int:
    """Insert one raw Remotive payload into ``raw.remotive_jobs`` and return the row id.

    ``ingested_at`` and ``source`` are filled by the table defaults
    (see sql/001_create_raw_remotive_jobs.sql).

    Args:
        payload: raw JSON payload as returned by the Remotive API.
        dsn: PostgreSQL connection string; defaults to the DATABASE_URL
            environment variable.

    Raises:
        ConfigError: if no ``dsn`` is given and DATABASE_URL is not set.
        psycopg.Error: if the connection or the insert fails.
    """
    conn_str = dsn or database_url()
    with psycopg.connect(conn_str) as conn, conn.cursor() as cur:
        cur.execute(_INSERT_RAW_REMOTIVE, (Jsonb(payload),))
        row = cur.fetchone()
        if row is None:  # defensive: RETURNING always yields one row on success
            raise psycopg.DataError("INSERT ... RETURNING did not return an id")
        return int(row[0])
