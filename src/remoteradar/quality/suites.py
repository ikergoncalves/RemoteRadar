"""Expectation suites for every warehouse layer (raw, staging, marts).

Each ``build_*`` function returns a fresh :class:`ExpectationSuite`. Suites are
built at validation time (not serialized) so time-dependent bounds like "not in
the future" are evaluated against the current clock.

Several checks intentionally overlap with the dbt tests in ``transform/``:
dbt tests validate models *while they are built*, from inside the
transformation tool; these suites validate the *materialized data* from the
outside, independently of dbt. The overlap is the point — a bug in a dbt test,
a schema.yml edit, or a run that skipped tests cannot silently disable the
quality gate, because the second layer has no shared machinery with the first.
"""

from __future__ import annotations

import datetime as dt

import great_expectations as gx
from great_expectations.core import ExpectationSuite

# Floor for job publication timestamps. The tracked job boards are live-listing
# APIs (RemoteOK exists since 2015); anything published earlier than this in a
# *current listings* payload is almost certainly a parsing bug, not history.
PUBLISHED_AT_FLOOR = dt.datetime(2015, 1, 1, tzinfo=dt.UTC)

# Floor for our own ingestion timestamps: the pipeline did not exist before
# this date, so earlier values can only come from clock or load bugs.
INGESTED_AT_FLOOR = dt.datetime(2025, 1, 1, tzinfo=dt.UTC)

# Tolerance for "not in the future" checks, so an ingestion that happened
# milliseconds before validation (or minor clock skew between the warehouse
# and the machine running the checks) does not fail spuriously.
CLOCK_SKEW_TOLERANCE = dt.timedelta(minutes=5)

# Sources publish timestamps in their own timezone conventions; allow up to a
# day ahead of UTC "now" before calling a publication date "in the future".
PUBLISHED_AT_FUTURE_TOLERANCE = dt.timedelta(days=1)


def _now_utc() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def build_raw_jobs_suite() -> ExpectationSuite:
    """Suite ``raw_jobs`` — sanity of one raw landing table (any source).

    Runs against a *projection* of ``raw.<source>_jobs`` that derives two
    columns from the JSONB payload (see ``checks.raw_projection_query`` for
    the SQL and ``tests/conftest.py`` for the pandas twin used by the local
    logic tests):

    * ``job_count_declared`` — the payload's own ``job-count`` field.
    * ``job_count_actual``   — the real length of the ``jobs`` array
      (null when ``jobs`` is missing or not an array).

    Checks:

    * ``payload`` is never null — a row without a payload is a broken load
      (the DDL also enforces this, but the suite must not trust the DDL).
    * ``ingested_at`` is never null and sits between the project's birth and
      "now" (+ clock-skew tolerance): ingestion timestamps in the future or
      the distant past mean clock or load bugs, and they would silently skew
      the dedup-by-latest-ingestion logic in the staging layer.
    * ``job_count_declared`` equals ``job_count_actual`` — the extractors
      write ``job-count`` themselves, so a mismatch means the payload was
      truncated or the extractor consolidation logic regressed.
    """
    now = _now_utc()
    return ExpectationSuite(
        name="raw_jobs",
        expectations=[
            gx.expectations.ExpectColumnValuesToNotBeNull(column="payload"),
            gx.expectations.ExpectColumnValuesToNotBeNull(column="ingested_at"),
            gx.expectations.ExpectColumnValuesToBeBetween(
                column="ingested_at",
                min_value=INGESTED_AT_FLOOR,
                max_value=now + CLOCK_SKEW_TOLERANCE,
            ),
            gx.expectations.ExpectColumnValuesToNotBeNull(column="job_count_declared"),
            gx.expectations.ExpectColumnValuesToNotBeNull(column="job_count_actual"),
            gx.expectations.ExpectColumnPairValuesToBeEqual(
                column_A="job_count_declared",
                column_B="job_count_actual",
            ),
        ],
    )


def build_staging_jobs_suite() -> ExpectationSuite:
    """Suite ``staging_jobs`` — shape of one staging view (any source).

    Applies to ``stg_remotive_jobs``, ``stg_remoteok_jobs`` and
    ``stg_adzuna_jobs``, which share the same column contract.

    Checks:

    * ``job_id`` is never null and unique *within the view* (across sources
      ids may collide; ``int_jobs_normalized.job_key`` handles that). This
      duplicates the dbt ``not_null``/``unique`` tests on purpose — see the
      module docstring for why the two layers are independent, not redundant.
    * ``title`` is never null — a job without a title is unusable downstream
      and signals JSONB parsing drift in the staging SQL.
    * ``published_at``, when present, is neither before
      :data:`PUBLISHED_AT_FLOOR` nor further than a day into the future.
      Nulls are allowed (not every source dates every job; the time marts
      filter them out) — the range only guards *parsed* values against
      timestamp-format regressions.
    """
    now = _now_utc()
    return ExpectationSuite(
        name="staging_jobs",
        expectations=[
            gx.expectations.ExpectColumnValuesToNotBeNull(column="job_id"),
            gx.expectations.ExpectColumnValuesToBeUnique(column="job_id"),
            gx.expectations.ExpectColumnValuesToNotBeNull(column="title"),
            gx.expectations.ExpectColumnValuesToBeBetween(
                column="published_at",
                min_value=PUBLISHED_AT_FLOOR,
                max_value=now + PUBLISHED_AT_FUTURE_TOLERANCE,
            ),
        ],
    )


def build_mart_salary_ranges_suite() -> ExpectationSuite:
    """Suite ``mart_salary_ranges`` — integrity of the salary statistics mart.

    Checks:

    * ``grouping_level`` is never null and only takes the three documented
      grains (``source``, ``source_salary_source``, ``source_category``) —
      the dashboard filters on this column, so an unexpected label would make
      rows invisible or double-counted.
    * ``job_count`` is never null and at least 1 — every row is a GROUP BY
      aggregate over at least one job; 0 or null means the aggregation broke.
    * ``min_salary_usd <= max_salary_usd`` whenever both are present
      (rows with only one bound are ignored). Equivalent to the dbt
      ``min_not_greater_than_max`` test, but asserted on the materialized
      table instead of during the dbt run.
    """
    return ExpectationSuite(
        name="mart_salary_ranges",
        expectations=[
            gx.expectations.ExpectColumnValuesToNotBeNull(column="grouping_level"),
            gx.expectations.ExpectColumnValuesToBeInSet(
                column="grouping_level",
                value_set=["source", "source_salary_source", "source_category"],
            ),
            gx.expectations.ExpectColumnValuesToNotBeNull(column="job_count"),
            gx.expectations.ExpectColumnValuesToBeBetween(column="job_count", min_value=1),
            gx.expectations.ExpectColumnPairValuesAToBeGreaterThanB(
                column_A="max_salary_usd",
                column_B="min_salary_usd",
                or_equal=True,
                ignore_row_if="either_value_is_missing",
            ),
        ],
    )


def build_mart_companies_suite() -> ExpectationSuite:
    """Suite ``mart_companies`` — integrity of the companies mart.

    Checks:

    * ``company_key`` is never null and unique — it is the mart's grouping
      key (lowercased/trimmed company name); duplicates would mean the
      GROUP BY grain broke and the dashboard would show split counts.
    * ``job_count`` is never null and at least 1 — the mart only aggregates
      jobs with a non-empty company, so every row must count something.
    """
    return ExpectationSuite(
        name="mart_companies",
        expectations=[
            gx.expectations.ExpectColumnValuesToNotBeNull(column="company_key"),
            gx.expectations.ExpectColumnValuesToBeUnique(column="company_key"),
            gx.expectations.ExpectColumnValuesToNotBeNull(column="job_count"),
            gx.expectations.ExpectColumnValuesToBeBetween(column="job_count", min_value=1),
        ],
    )


def build_mart_jobs_over_time_suite() -> ExpectationSuite:
    """Suite ``mart_jobs_over_time`` — integrity of the weekly time series.

    Checks:

    * ``grouping_level`` is never null and only takes the two documented
      grains (``source``, ``source_skill``) — same dashboard-filter rationale
      as in ``mart_salary_ranges``.
    * ``week_start`` is never null — the mart excludes jobs without
      ``published_at``, so a null bucket means the filter regressed.
    * ``job_count`` is never null and at least 1 — every row aggregates at
      least one job.
    """
    return ExpectationSuite(
        name="mart_jobs_over_time",
        expectations=[
            gx.expectations.ExpectColumnValuesToNotBeNull(column="grouping_level"),
            gx.expectations.ExpectColumnValuesToBeInSet(
                column="grouping_level",
                value_set=["source", "source_skill"],
            ),
            gx.expectations.ExpectColumnValuesToNotBeNull(column="week_start"),
            gx.expectations.ExpectColumnValuesToNotBeNull(column="job_count"),
            gx.expectations.ExpectColumnValuesToBeBetween(column="job_count", min_value=1),
        ],
    )
