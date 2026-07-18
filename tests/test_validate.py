"""Tests for the warehouse validation module (no database required)."""

from __future__ import annotations

import pytest
from great_expectations.core import ExpectationSuite

from remoteradar.config import ConfigError
from remoteradar.quality.checks import WAREHOUSE_CHECKS, WarehouseCheck, raw_projection_query
from remoteradar.quality.suites import build_raw_jobs_suite
from remoteradar.validate import CheckResult, _to_sqlalchemy_url, format_report, run_checks


def test_to_sqlalchemy_url_upgrades_plain_postgres_schemes():
    assert (
        _to_sqlalchemy_url("postgresql://user:pass@host:5432/db")
        == "postgresql+psycopg://user:pass@host:5432/db"
    )
    assert (
        _to_sqlalchemy_url("postgres://user:pass@host:5432/db")
        == "postgresql+psycopg://user:pass@host:5432/db"
    )


def test_to_sqlalchemy_url_keeps_explicit_drivers():
    dsn = "postgresql+psycopg2://user:pass@host:5432/db"
    assert _to_sqlalchemy_url(dsn) == dsn


def test_warehouse_checks_registry_is_consistent():
    names = [check.name for check in WAREHOUSE_CHECKS]
    assert len(names) == len(set(names)), "check names must be unique (they name GX assets)"
    for check in WAREHOUSE_CHECKS:
        suite = check.suite()
        assert isinstance(suite, ExpectationSuite)
        assert suite.expectations, f"suite for {check.name} must not be empty"
        if check.layer == "raw":
            assert check.query is not None
        else:
            assert check.table_name is not None


def test_warehouse_checks_cover_every_layer_table():
    by_layer = {layer: [] for layer in ("raw", "staging", "marts")}
    for check in WAREHOUSE_CHECKS:
        by_layer[check.layer].append(check.name)
    assert sorted(by_layer["raw"]) == ["raw_adzuna_jobs", "raw_remoteok_jobs", "raw_remotive_jobs"]
    assert sorted(by_layer["staging"]) == [
        "stg_adzuna_jobs",
        "stg_remoteok_jobs",
        "stg_remotive_jobs",
    ]
    assert sorted(by_layer["marts"]) == [
        "mart_companies",
        "mart_jobs_over_time",
        "mart_salary_ranges",
    ]


def test_raw_projection_query_targets_the_raw_schema():
    query = raw_projection_query("remotive_jobs")
    assert "from raw.remotive_jobs" in query
    assert "job_count_declared" in query and "job_count_actual" in query


def test_warehouse_check_requires_exactly_one_target():
    with pytest.raises(ValueError):
        WarehouseCheck(name="bad", suite=build_raw_jobs_suite, layer="raw")
    with pytest.raises(ValueError):
        WarehouseCheck(
            name="bad",
            suite=build_raw_jobs_suite,
            layer="raw",
            table_name="t",
            query="select 1",
        )


def test_run_checks_without_database_url_raises_config_error(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(ConfigError):
        run_checks()


def test_format_report_lists_outcomes_and_summary():
    results = [
        CheckResult(
            check="raw_remotive_jobs",
            suite="raw_jobs",
            status="passed",
            passed_expectations=6,
            total_expectations=6,
        ),
        CheckResult(
            check="stg_adzuna_jobs",
            suite="staging_jobs",
            status="failed",
            passed_expectations=3,
            total_expectations=4,
            failures=["expect_column_values_to_not_be_null on title: 2 unexpected value(s)"],
        ),
        CheckResult(
            check="mart_companies",
            suite="mart_companies",
            status="error",
            error='relation "analytics.mart_companies" does not exist',
        ),
    ]

    report = format_report(results)

    assert "PASSED  raw_remotive_jobs (suite raw_jobs, 6/6 expectations)" in report
    assert "FAILED  stg_adzuna_jobs (suite staging_jobs, 3/4 expectations)" in report
    assert "- expect_column_values_to_not_be_null on title: 2 unexpected value(s)" in report
    assert "ERROR   mart_companies (suite mart_companies)" in report
    assert 'relation "analytics.mart_companies" does not exist' in report
    assert "Summary: 1 passed, 1 failed, 1 error(s)" in report
