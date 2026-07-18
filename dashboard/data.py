"""Data access and shaping for the RemoteRadar dashboard.

Pure data layer: no Streamlit imports. Every shaping function takes DataFrames
and returns a plot-ready DataFrame, so the whole module is testable without
running Streamlit (see ``tests/test_dashboard_data.py``).

Loading tries the warehouse (the dbt marts under ``DBT_PG_SCHEMA``) and falls
back to the bundled sample CSVs in ``dashboard/sample_data/`` when
``DATABASE_URL`` is missing or the connection fails — the UI shows a banner
whenever the fallback is active.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Protocol

import pandas as pd
import sqlalchemy

SAMPLE_DATA_DIR = Path(__file__).parent / "sample_data"

# Mirrors remoteradar.config.DEFAULT_ANALYTICS_SCHEMA / transform/profiles.yml
# (not imported: this package must stay installable without the pipeline).
DEFAULT_ANALYTICS_SCHEMA = "analytics"

# Whitelist of mart tables read from the warehouse; never interpolate caller
# input into queries (same approach as remoteradar.quality.checks).
MART_TABLES = ("mart_skills", "mart_salary_ranges", "mart_companies", "mart_jobs_over_time")

SALARY_GROUPING_LEVELS = ("source", "source_salary_source", "source_category")
TIME_GROUPING_LEVELS = ("source", "source_skill")

# Human-facing labels for the salary confidence flag set in int_jobs_normalized.
# structured_converted comes from a FIXED approximate exchange rate (documented
# limitation of the dbt seed), so it is marked as approximate everywhere.
SALARY_SOURCE_LABELS = {
    "structured_usd": "USD as posted",
    "structured_converted": "≈ converted (fixed FX rate)",
}
MIXED_CONFIDENCE_LABEL = "not broken down — may include fixed-rate conversions"

_REQUIRED_COLUMNS = {
    "mart_skills": {"job_key", "source", "skill", "skill_type", "published_at"},
    "mart_salary_ranges": {
        "grouping_level",
        "source",
        "salary_source",
        "category",
        "job_count",
        "avg_salary_usd",
        "median_salary_usd",
        "min_salary_usd",
        "max_salary_usd",
    },
    "mart_companies": {
        "company_key",
        "company",
        "job_count",
        "source_count",
        "sources",
        "first_published_at",
        "last_published_at",
    },
    "mart_jobs_over_time": {"grouping_level", "week_start", "source", "skill", "job_count"},
}


@dataclass(frozen=True)
class DashboardData:
    """The four dbt marts plus where they came from (warehouse vs sample)."""

    skills: pd.DataFrame
    salary_ranges: pd.DataFrame
    companies: pd.DataFrame
    jobs_over_time: pd.DataFrame
    is_demo: bool
    demo_reason: str | None = None


class WarehouseLoader(Protocol):
    def __call__(self, database_url: str, schema: str | None = None) -> DashboardData: ...


def to_sqlalchemy_url(dsn: str) -> str:
    """Rewrite a plain postgres DSN to use the psycopg (v3) SQLAlchemy driver.

    Twin of ``remoteradar.validate._to_sqlalchemy_url`` (not imported — see
    module docstring): bare ``postgresql://`` URLs make SQLAlchemy default to
    psycopg2, which this project does not install.
    """
    for prefix in ("postgresql://", "postgres://"):
        if dsn.startswith(prefix):
            return "postgresql+psycopg://" + dsn.removeprefix(prefix)
    return dsn


def _coerce_types(name: str, frame: pd.DataFrame) -> pd.DataFrame:
    """Normalize dtypes so warehouse and CSV loads look identical downstream."""
    missing = _REQUIRED_COLUMNS[name] - set(frame.columns)
    if missing:
        raise ValueError(f"{name} is missing expected columns: {sorted(missing)}")
    frame = frame.copy()
    if name == "mart_skills":
        frame["published_at"] = pd.to_datetime(frame["published_at"], utc=True)
    elif name == "mart_salary_ranges":
        for column in (
            "job_count",
            "avg_salary_usd",
            "median_salary_usd",
            "min_salary_usd",
            "max_salary_usd",
        ):
            frame[column] = pd.to_numeric(frame[column])
    elif name == "mart_companies":
        for column in ("first_published_at", "last_published_at"):
            frame[column] = pd.to_datetime(frame[column], utc=True)
    elif name == "mart_jobs_over_time":
        frame["week_start"] = pd.to_datetime(frame["week_start"])
    return frame


def _to_dashboard_data(
    frames: dict[str, pd.DataFrame], *, is_demo: bool, demo_reason: str | None = None
) -> DashboardData:
    coerced = {name: _coerce_types(name, frame) for name, frame in frames.items()}
    return DashboardData(
        skills=coerced["mart_skills"],
        salary_ranges=coerced["mart_salary_ranges"],
        companies=coerced["mart_companies"],
        jobs_over_time=coerced["mart_jobs_over_time"],
        is_demo=is_demo,
        demo_reason=demo_reason,
    )


def load_sample_data(sample_dir: Path = SAMPLE_DATA_DIR) -> DashboardData:
    """Load the bundled sample marts (demo mode)."""
    frames = {name: pd.read_csv(sample_dir / f"{name}.csv") for name in MART_TABLES}
    return _to_dashboard_data(frames, is_demo=True)


def load_warehouse_data(database_url: str, schema: str | None = None) -> DashboardData:
    """Read the four marts from the warehouse. Raises on any connection/SQL error."""
    schema = schema or os.environ.get("DBT_PG_SCHEMA") or DEFAULT_ANALYTICS_SCHEMA
    engine = sqlalchemy.create_engine(to_sqlalchemy_url(database_url))
    try:
        with engine.connect() as connection:
            frames = {
                name: pd.read_sql_query(
                    sqlalchemy.text(f'select * from "{schema}".{name}'), connection
                )
                for name in MART_TABLES
            }
    finally:
        engine.dispose()
    return _to_dashboard_data(frames, is_demo=False)


def load_dashboard_data(
    database_url: str | None = None,
    schema: str | None = None,
    warehouse_loader: WarehouseLoader = load_warehouse_data,
    sample_loader=load_sample_data,
) -> DashboardData:
    """Warehouse data when reachable, sample data otherwise.

    The loaders are injectable so tests can exercise the fallback without a
    real database. Any exception from the warehouse loader triggers the
    fallback on purpose — for a portfolio dashboard, showing labelled sample
    data beats an error page.
    """
    if not database_url:
        return replace(
            sample_loader(), demo_reason="DATABASE_URL is not configured"
        )
    try:
        return warehouse_loader(database_url, schema)
    except Exception as exc:  # noqa: BLE001 — deliberate demo fallback
        return replace(
            sample_loader(),
            demo_reason=f"could not read the warehouse ({type(exc).__name__})",
        )


# --- shaping helpers (pure; take frames, return plot-ready frames) -----------


def source_options(skills: pd.DataFrame) -> list[str]:
    """Sources present in the data, for the filter widget."""
    return sorted(skills["source"].dropna().unique())


def top_skills(
    skills: pd.DataFrame,
    *,
    sources: list[str] | None = None,
    skill_types: list[str] | None = None,
    limit: int = 15,
) -> pd.DataFrame:
    """Most requested skills as ``skill, job_count``, descending.

    Counts distinct job_key per skill: mart_skills has one row per job per
    (skill, skill_type), so a job whose category equals one of its tags would
    otherwise count twice.
    """
    filtered = skills
    if sources is not None:
        filtered = filtered[filtered["source"].isin(sources)]
    if skill_types is not None:
        filtered = filtered[filtered["skill_type"].isin(skill_types)]
    return (
        filtered.groupby("skill")["job_key"]
        .nunique()
        .rename("job_count")
        .sort_values(ascending=False)
        .head(limit)
        .reset_index()
    )


def salary_breakdown(
    salary_ranges: pd.DataFrame,
    grouping_level: str,
    *,
    sources: list[str] | None = None,
) -> pd.DataFrame:
    """Rows of one grouping level, labelled for display — never re-aggregated.

    The mart pre-computes avg/median per level precisely so the dashboard does
    not average averages; this function only filters, labels and sorts.
    ``confidence_label`` carries the salary_source flag (or an explicit
    "not broken down" note at levels that mix confidence classes).
    """
    if grouping_level not in SALARY_GROUPING_LEVELS:
        raise ValueError(
            f"unknown grouping_level {grouping_level!r}; expected one of {SALARY_GROUPING_LEVELS}"
        )
    rows = salary_ranges[salary_ranges["grouping_level"] == grouping_level].copy()
    if sources is not None:
        rows = rows[rows["source"].isin(sources)]
    if grouping_level == "source":
        rows["group_label"] = rows["source"]
        rows["confidence_label"] = MIXED_CONFIDENCE_LABEL
    elif grouping_level == "source_salary_source":
        rows["confidence_label"] = rows["salary_source"].map(SALARY_SOURCE_LABELS)
        rows["group_label"] = rows["source"] + " · " + rows["confidence_label"]
    else:  # source_category
        rows["group_label"] = rows["source"] + " · " + rows["category"]
        rows["confidence_label"] = MIXED_CONFIDENCE_LABEL
    return rows.sort_values("avg_salary_usd", ascending=False).reset_index(drop=True)


def top_companies(companies: pd.DataFrame, *, limit: int = 15) -> pd.DataFrame:
    """Companies with the most postings. No source filter: each mart row spans
    every source the company appears in, so filtering would misstate counts."""
    return (
        companies.sort_values(["job_count", "company_key"], ascending=[False, True])
        .head(limit)
        .reset_index(drop=True)
    )


def jobs_over_time_series(
    jobs_over_time: pd.DataFrame,
    grouping_level: str = "source",
    *,
    sources: list[str] | None = None,
    skills: list[str] | None = None,
) -> pd.DataFrame:
    """Weekly counts as ``week_start, series, job_count`` for the line chart.

    ``series`` is the source (level ``source``) or the skill (level
    ``source_skill``, summed across the selected sources — counts are
    additive, unlike the salary averages, so this sum is safe).
    """
    if grouping_level not in TIME_GROUPING_LEVELS:
        raise ValueError(
            f"unknown grouping_level {grouping_level!r}; expected one of {TIME_GROUPING_LEVELS}"
        )
    rows = jobs_over_time[jobs_over_time["grouping_level"] == grouping_level]
    if sources is not None:
        rows = rows[rows["source"].isin(sources)]
    if grouping_level == "source":
        out = rows.rename(columns={"source": "series"})[["week_start", "series", "job_count"]]
        return (
            out.groupby(["week_start", "series"], as_index=False)["job_count"]
            .sum()
            .sort_values(["week_start", "series"])
            .reset_index(drop=True)
        )
    if skills is not None:
        rows = rows[rows["skill"].isin(skills)]
    return (
        rows.rename(columns={"skill": "series"})
        .groupby(["week_start", "series"], as_index=False)["job_count"]
        .sum()
        .sort_values(["week_start", "series"])
        .reset_index(drop=True)
    )


def category_only_skills(skills: pd.DataFrame) -> set[str]:
    """Labels that only ever appear as a board category, never as a tag.

    mart_jobs_over_time drops skill_type, so broad board labels ("it jobs",
    "software development") rank alongside real skills; this set lets the
    skill picker exclude them without re-modelling the mart.
    """
    tags = set(skills.loc[skills["skill_type"] == "tag", "skill"])
    categories = set(skills.loc[skills["skill_type"] == "category", "skill"])
    return categories - tags


def top_time_skills(
    jobs_over_time: pd.DataFrame, *, limit: int = 8, exclude: set[str] | None = None
) -> list[str]:
    """Skills with the highest total weekly counts, for the skill picker.

    Also used as the fixed color domain of the by-skill line chart, so a
    skill keeps its color when the selection changes.
    """
    rows = jobs_over_time[jobs_over_time["grouping_level"] == "source_skill"]
    if exclude:
        rows = rows[~rows["skill"].isin(exclude)]
    totals = rows.groupby("skill")["job_count"].sum().sort_values(ascending=False)
    return list(totals.head(limit).index)
