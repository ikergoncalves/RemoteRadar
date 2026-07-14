"""Loading of raw API payloads into the PostgreSQL warehouse (schema ``raw``)."""

from __future__ import annotations

from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from remoteradar.config import database_url

# Source name -> landing table. Table names are looked up here instead of being
# interpolated from caller input, so the SQL below never receives an arbitrary
# identifier. DDL for each table lives in sql/.
RAW_TABLES: dict[str, str] = {
    "remotive": "raw.remotive_jobs",
    "remoteok": "raw.remoteok_jobs",
    "adzuna": "raw.adzuna_jobs",
}


def insert_raw_payload(source: str, payload: dict[str, Any], *, dsn: str | None = None) -> int:
    """Insert one raw payload into the source's landing table and return the row id.

    ``ingested_at`` and ``source`` are filled by the table defaults
    (see the corresponding DDL in sql/).

    Args:
        source: source name, one of :data:`RAW_TABLES` (``remotive``,
            ``remoteok``, ``adzuna``).
        payload: raw JSON payload as returned by the source's API.
        dsn: PostgreSQL connection string; defaults to the DATABASE_URL
            environment variable.

    Raises:
        ValueError: if ``source`` is not a known source name.
        ConfigError: if no ``dsn`` is given and DATABASE_URL is not set.
        psycopg.Error: if the connection or the insert fails.
    """
    table = RAW_TABLES.get(source)
    if table is None:
        raise ValueError(f"Unknown source {source!r}; expected one of {sorted(RAW_TABLES)}")
    conn_str = dsn or database_url()
    query = f"INSERT INTO {table} (payload) VALUES (%s) RETURNING id"
    with psycopg.connect(conn_str) as conn, conn.cursor() as cur:
        cur.execute(query, (Jsonb(payload),))
        row = cur.fetchone()
        if row is None:  # defensive: RETURNING always yields one row on success
            raise psycopg.DataError("INSERT ... RETURNING did not return an id")
        return int(row[0])
