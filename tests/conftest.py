"""Shared fixtures for the data quality logic tests.

Loads the sample data in ``tests/fixtures/`` — one table per warehouse layer
(raw, staging, marts) — as pandas DataFrames shaped exactly like what the
expectation suites see: raw rows go through :func:`project_raw_payloads`, the
pandas twin of the SQL projection used against the real warehouse.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

# Keep GX telemetry off for local runs and CI; must be set before the first
# great_expectations import (pytest imports conftest before test modules).
os.environ.setdefault("GX_ANALYTICS_ENABLED", "false")

import great_expectations as gx  # noqa: E402
import pandas as pd  # noqa: E402
import pytest  # noqa: E402
from great_expectations.core import ExpectationSuite  # noqa: E402
from great_expectations.core.expectation_validation_result import (  # noqa: E402
    ExpectationSuiteValidationResult,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def project_raw_payloads(rows: list[dict[str, Any]]) -> pd.DataFrame:
    """Pandas twin of ``remoteradar.quality.checks.raw_projection_query``.

    Derives ``job_count_declared`` and ``job_count_actual`` from each raw
    payload the same way the SQL projection does (non-dict payloads and
    missing/non-array ``jobs`` become nulls). Keep the two in sync.
    """

    def declared(payload: Any) -> int | None:
        return payload.get("job-count") if isinstance(payload, dict) else None

    def actual(payload: Any) -> int | None:
        jobs = payload.get("jobs") if isinstance(payload, dict) else None
        return len(jobs) if isinstance(jobs, list) else None

    payloads = [row["payload"] for row in rows]
    return pd.DataFrame(
        {
            "id": [row["id"] for row in rows],
            "ingested_at": pd.to_datetime([row["ingested_at"] for row in rows], utc=True),
            "payload": payloads,
            "job_count_declared": [declared(payload) for payload in payloads],
            "job_count_actual": [actual(payload) for payload in payloads],
        }
    )


@pytest.fixture()
def validate_frame():
    """Run an expectation suite against a DataFrame in an ephemeral GX context."""

    def _validate(
        frame: pd.DataFrame, suite: ExpectationSuite
    ) -> ExpectationSuiteValidationResult:
        context = gx.get_context(mode="ephemeral")
        batch_definition = (
            context.data_sources.add_pandas(name="local_sample")
            .add_dataframe_asset(name="sample_asset")
            .add_batch_definition_whole_dataframe("sample_batch")
        )
        batch = batch_definition.get_batch(batch_parameters={"dataframe": frame})
        return batch.validate(suite)

    return _validate


@pytest.fixture()
def raw_payload_rows() -> list[dict[str, Any]]:
    with open(FIXTURES_DIR / "raw_remotive_jobs.json", encoding="utf-8") as handle:
        return json.load(handle)


@pytest.fixture()
def raw_jobs_frame(raw_payload_rows: list[dict[str, Any]]) -> pd.DataFrame:
    return project_raw_payloads(raw_payload_rows)


@pytest.fixture()
def stg_jobs_frame() -> pd.DataFrame:
    frame = pd.read_csv(FIXTURES_DIR / "stg_jobs.csv")
    frame["published_at"] = pd.to_datetime(frame["published_at"], utc=True)
    return frame


@pytest.fixture()
def mart_salary_ranges_frame() -> pd.DataFrame:
    return pd.read_csv(FIXTURES_DIR / "mart_salary_ranges.csv")


@pytest.fixture()
def mart_companies_frame() -> pd.DataFrame:
    frame = pd.read_csv(FIXTURES_DIR / "mart_companies.csv")
    for column in ("first_published_at", "last_published_at"):
        frame[column] = pd.to_datetime(frame[column], utc=True)
    return frame


@pytest.fixture()
def mart_jobs_over_time_frame() -> pd.DataFrame:
    frame = pd.read_csv(FIXTURES_DIR / "mart_jobs_over_time.csv")
    frame["week_start"] = pd.to_datetime(frame["week_start"])
    return frame
