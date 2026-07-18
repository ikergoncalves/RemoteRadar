"""Binding of expectation suites to concrete warehouse tables and queries.

:data:`WAREHOUSE_CHECKS` is the registry :mod:`remoteradar.validate` iterates:
each entry names one check, the suite it runs and where the data lives. Raw
checks use a SQL projection (the suite needs job-count columns derived from
the JSONB payload); staging and mart checks read their table/view directly in
the dbt schema (``DBT_PG_SCHEMA``, resolved at run time).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from great_expectations.core import ExpectationSuite

from remoteradar.quality.suites import (
    build_mart_companies_suite,
    build_mart_jobs_over_time_suite,
    build_mart_salary_ranges_suite,
    build_raw_jobs_suite,
    build_staging_jobs_suite,
)

# Raw landing tables live in the fixed schema created by sql/*.sql; identifiers
# are hardcoded here (never interpolated from caller input), mirroring the
# whitelist approach of remoteradar.load.RAW_TABLES.
_RAW_SCHEMA = "raw"


def raw_projection_query(table: str) -> str:
    """SQL projecting one raw landing table into the shape ``raw_jobs`` expects.

    Derives the two job-count columns the suite compares. ``jsonb_typeof``
    guards the length call: a missing or non-array ``jobs`` key becomes a null
    ``job_count_actual`` (which the suite flags) instead of a query error.

    The pandas twin of this projection lives in ``tests/conftest.py``
    (``project_raw_payloads``); keep the two in sync.
    """
    return f"""
        select
            id,
            ingested_at,
            payload,
            (payload ->> 'job-count')::int as job_count_declared,
            case
                when jsonb_typeof(payload -> 'jobs') = 'array'
                    then jsonb_array_length(payload -> 'jobs')
            end as job_count_actual
        from {_RAW_SCHEMA}.{table}
    """


@dataclass(frozen=True)
class WarehouseCheck:
    """One validation to run against the warehouse.

    Exactly one of ``table_name`` (read as-is from the dbt schema) or
    ``query`` (schema-qualified SQL projection) is set.
    """

    name: str
    suite: Callable[[], ExpectationSuite]
    layer: Literal["raw", "staging", "marts"]
    table_name: str | None = None
    query: str | None = None

    def __post_init__(self) -> None:
        if (self.table_name is None) == (self.query is None):
            raise ValueError(f"Check {self.name!r} must set exactly one of table_name/query")


WAREHOUSE_CHECKS: tuple[WarehouseCheck, ...] = (
    WarehouseCheck(
        name="raw_remotive_jobs",
        suite=build_raw_jobs_suite,
        layer="raw",
        query=raw_projection_query("remotive_jobs"),
    ),
    WarehouseCheck(
        name="raw_remoteok_jobs",
        suite=build_raw_jobs_suite,
        layer="raw",
        query=raw_projection_query("remoteok_jobs"),
    ),
    WarehouseCheck(
        name="raw_adzuna_jobs",
        suite=build_raw_jobs_suite,
        layer="raw",
        query=raw_projection_query("adzuna_jobs"),
    ),
    WarehouseCheck(
        name="stg_remotive_jobs",
        suite=build_staging_jobs_suite,
        layer="staging",
        table_name="stg_remotive_jobs",
    ),
    WarehouseCheck(
        name="stg_remoteok_jobs",
        suite=build_staging_jobs_suite,
        layer="staging",
        table_name="stg_remoteok_jobs",
    ),
    WarehouseCheck(
        name="stg_adzuna_jobs",
        suite=build_staging_jobs_suite,
        layer="staging",
        table_name="stg_adzuna_jobs",
    ),
    WarehouseCheck(
        name="mart_salary_ranges",
        suite=build_mart_salary_ranges_suite,
        layer="marts",
        table_name="mart_salary_ranges",
    ),
    WarehouseCheck(
        name="mart_companies",
        suite=build_mart_companies_suite,
        layer="marts",
        table_name="mart_companies",
    ),
    WarehouseCheck(
        name="mart_jobs_over_time",
        suite=build_mart_jobs_over_time_suite,
        layer="marts",
        table_name="mart_jobs_over_time",
    ),
)
