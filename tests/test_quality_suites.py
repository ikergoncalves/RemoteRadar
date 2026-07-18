"""Logic tests for the expectation suites, run against local sample data.

These execute the exact suites the warehouse validation
(``remoteradar-validate``) runs, but against the small pandas frames built
from ``tests/fixtures/`` — no PostgreSQL involved. A healthy fixture must
pass its suite, and each targeted corruption must fail the expectation that
guards against it. This proves the suites' *logic*; it says nothing about
the health of the real warehouse (that validation stays pending until a
database is available).
"""

from __future__ import annotations

import pandas as pd

from conftest import project_raw_payloads
from remoteradar.quality.suites import (
    build_mart_companies_suite,
    build_mart_jobs_over_time_suite,
    build_mart_salary_ranges_suite,
    build_raw_jobs_suite,
    build_staging_jobs_suite,
)


def failures(result) -> set[tuple[str, str | None]]:
    """(expectation type, column) pairs for every failed expectation."""
    return {
        (
            r.expectation_config.type,
            r.expectation_config.kwargs.get("column")
            or r.expectation_config.kwargs.get("column_A"),
        )
        for r in result.results
        if not r.success
    }


# --- raw layer ---------------------------------------------------------------


def test_raw_fixture_passes(validate_frame, raw_jobs_frame):
    result = validate_frame(raw_jobs_frame, build_raw_jobs_suite())
    assert result.success, failures(result)


def test_raw_null_payload_fails(validate_frame, raw_jobs_frame):
    raw_jobs_frame.loc[0, "payload"] = None
    result = validate_frame(raw_jobs_frame, build_raw_jobs_suite())
    assert ("expect_column_values_to_not_be_null", "payload") in failures(result)


def test_raw_job_count_mismatch_fails(validate_frame, raw_payload_rows):
    raw_payload_rows[0]["payload"]["job-count"] = 99  # payload declares more jobs than it carries
    result = validate_frame(project_raw_payloads(raw_payload_rows), build_raw_jobs_suite())
    assert ("expect_column_pair_values_to_be_equal", "job_count_declared") in failures(result)


def test_raw_non_array_jobs_fails(validate_frame, raw_payload_rows):
    raw_payload_rows[0]["payload"]["jobs"] = {"unexpected": "object"}
    result = validate_frame(project_raw_payloads(raw_payload_rows), build_raw_jobs_suite())
    assert ("expect_column_values_to_not_be_null", "job_count_actual") in failures(result)


def test_raw_future_ingested_at_fails(validate_frame, raw_jobs_frame):
    raw_jobs_frame.loc[0, "ingested_at"] = pd.Timestamp.now(tz="UTC") + pd.Timedelta(hours=2)
    result = validate_frame(raw_jobs_frame, build_raw_jobs_suite())
    assert ("expect_column_values_to_be_between", "ingested_at") in failures(result)


def test_project_raw_payloads_derives_job_counts():
    ingested = "2026-07-01T00:00:00+00:00"
    rows = [
        {"id": 1, "ingested_at": ingested, "payload": {"job-count": 0, "jobs": []}},
        {"id": 2, "ingested_at": ingested, "payload": {"jobs": "not-a-list"}},
        {"id": 3, "ingested_at": ingested, "payload": None},
    ]
    frame = project_raw_payloads(rows)
    for column in ("job_count_declared", "job_count_actual"):
        assert frame[column].iloc[0] == 0
        assert frame[column].iloc[1:].isna().all(), f"{column} must be null for rows 2 and 3"


# --- staging layer -----------------------------------------------------------


def test_staging_fixture_passes(validate_frame, stg_jobs_frame):
    # The fixture includes a job with a null published_at on purpose: the
    # suite allows nulls there and only range-checks parsed values.
    assert stg_jobs_frame["published_at"].isna().any()
    result = validate_frame(stg_jobs_frame, build_staging_jobs_suite())
    assert result.success, failures(result)


def test_staging_duplicate_job_id_fails(validate_frame, stg_jobs_frame):
    stg_jobs_frame.loc[1, "job_id"] = stg_jobs_frame.loc[0, "job_id"]
    result = validate_frame(stg_jobs_frame, build_staging_jobs_suite())
    assert ("expect_column_values_to_be_unique", "job_id") in failures(result)


def test_staging_null_title_fails(validate_frame, stg_jobs_frame):
    stg_jobs_frame.loc[0, "title"] = None
    result = validate_frame(stg_jobs_frame, build_staging_jobs_suite())
    assert ("expect_column_values_to_not_be_null", "title") in failures(result)


def test_staging_prehistoric_published_at_fails(validate_frame, stg_jobs_frame):
    stg_jobs_frame.loc[0, "published_at"] = pd.Timestamp("1999-12-31", tz="UTC")
    result = validate_frame(stg_jobs_frame, build_staging_jobs_suite())
    assert ("expect_column_values_to_be_between", "published_at") in failures(result)


def test_staging_far_future_published_at_fails(validate_frame, stg_jobs_frame):
    stg_jobs_frame.loc[0, "published_at"] = pd.Timestamp.now(tz="UTC") + pd.Timedelta(days=30)
    result = validate_frame(stg_jobs_frame, build_staging_jobs_suite())
    assert ("expect_column_values_to_be_between", "published_at") in failures(result)


# --- marts layer -------------------------------------------------------------


def test_mart_salary_ranges_fixture_passes(validate_frame, mart_salary_ranges_frame):
    # The fixture includes a row with only max_salary_usd set: the min<=max
    # pair check must ignore rows where either bound is missing.
    assert mart_salary_ranges_frame["min_salary_usd"].isna().any()
    result = validate_frame(mart_salary_ranges_frame, build_mart_salary_ranges_suite())
    assert result.success, failures(result)


def test_mart_salary_ranges_unknown_grouping_level_fails(validate_frame, mart_salary_ranges_frame):
    mart_salary_ranges_frame.loc[0, "grouping_level"] = "by_country"
    result = validate_frame(mart_salary_ranges_frame, build_mart_salary_ranges_suite())
    assert ("expect_column_values_to_be_in_set", "grouping_level") in failures(result)


def test_mart_salary_ranges_zero_job_count_fails(validate_frame, mart_salary_ranges_frame):
    mart_salary_ranges_frame.loc[0, "job_count"] = 0
    result = validate_frame(mart_salary_ranges_frame, build_mart_salary_ranges_suite())
    assert ("expect_column_values_to_be_between", "job_count") in failures(result)


def test_mart_salary_ranges_min_above_max_fails(validate_frame, mart_salary_ranges_frame):
    mart_salary_ranges_frame.loc[0, "min_salary_usd"] = 999999
    result = validate_frame(mart_salary_ranges_frame, build_mart_salary_ranges_suite())
    assert ("expect_column_pair_values_a_to_be_greater_than_b", "max_salary_usd") in failures(
        result
    )


def test_mart_companies_fixture_passes(validate_frame, mart_companies_frame):
    result = validate_frame(mart_companies_frame, build_mart_companies_suite())
    assert result.success, failures(result)


def test_mart_companies_duplicate_key_fails(validate_frame, mart_companies_frame):
    mart_companies_frame.loc[1, "company_key"] = mart_companies_frame.loc[0, "company_key"]
    result = validate_frame(mart_companies_frame, build_mart_companies_suite())
    assert ("expect_column_values_to_be_unique", "company_key") in failures(result)


def test_mart_companies_zero_job_count_fails(validate_frame, mart_companies_frame):
    mart_companies_frame.loc[0, "job_count"] = 0
    result = validate_frame(mart_companies_frame, build_mart_companies_suite())
    assert ("expect_column_values_to_be_between", "job_count") in failures(result)


def test_mart_jobs_over_time_fixture_passes(validate_frame, mart_jobs_over_time_frame):
    result = validate_frame(mart_jobs_over_time_frame, build_mart_jobs_over_time_suite())
    assert result.success, failures(result)


def test_mart_jobs_over_time_unknown_grouping_level_fails(
    validate_frame, mart_jobs_over_time_frame
):
    mart_jobs_over_time_frame.loc[0, "grouping_level"] = "daily"
    result = validate_frame(mart_jobs_over_time_frame, build_mart_jobs_over_time_suite())
    assert ("expect_column_values_to_be_in_set", "grouping_level") in failures(result)


def test_mart_jobs_over_time_null_week_start_fails(validate_frame, mart_jobs_over_time_frame):
    mart_jobs_over_time_frame.loc[0, "week_start"] = pd.NaT
    result = validate_frame(mart_jobs_over_time_frame, build_mart_jobs_over_time_suite())
    assert ("expect_column_values_to_not_be_null", "week_start") in failures(result)
