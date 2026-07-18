"""Data quality layer built on Great Expectations (GX Core 1.x, Fluent API).

This package is the project's Great Expectations home. Instead of the
scaffolded ``gx/`` directory (file-based Data Context with serialized YAML/JSON
suites), everything is defined as code and executed against ephemeral Data
Contexts:

* :mod:`remoteradar.quality.suites` — the expectation suites, one builder
  function per suite, documenting what each check validates and why.
* :mod:`remoteradar.quality.checks` — the binding between suites and concrete
  warehouse tables/queries, consumed by :mod:`remoteradar.validate`.

Code-defined suites keep a single reviewable source of truth in git (no
serialized copies to drift out of sync) and can be imported both by the
warehouse validation CLI (``remoteradar-validate``) and by the local Pytest
logic tests that run the same suites against sample pandas data without a
database.
"""

from remoteradar.quality.checks import WAREHOUSE_CHECKS, WarehouseCheck
from remoteradar.quality.suites import (
    build_mart_companies_suite,
    build_mart_jobs_over_time_suite,
    build_mart_salary_ranges_suite,
    build_raw_jobs_suite,
    build_staging_jobs_suite,
)

__all__ = [
    "WAREHOUSE_CHECKS",
    "WarehouseCheck",
    "build_mart_companies_suite",
    "build_mart_jobs_over_time_suite",
    "build_mart_salary_ranges_suite",
    "build_raw_jobs_suite",
    "build_staging_jobs_suite",
]
