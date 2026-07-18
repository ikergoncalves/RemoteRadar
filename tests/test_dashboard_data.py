"""Tests for the dashboard data layer (``dashboard.data``).

Runs entirely on the bundled sample CSVs and small hand-built frames — no
Streamlit, no database. The visual layer (``dashboard/app.py``) is exercised
manually; everything it plots comes from the functions covered here.
"""

from __future__ import annotations

import pandas as pd
import pytest

from dashboard import data as dd


@pytest.fixture(scope="module")
def sample() -> dd.DashboardData:
    return dd.load_sample_data()


# --- loading and demo fallback ----------------------------------------------


def test_sample_data_is_complete_and_typed(sample: dd.DashboardData):
    assert sample.is_demo
    for name, frame in (
        ("mart_skills", sample.skills),
        ("mart_salary_ranges", sample.salary_ranges),
        ("mart_companies", sample.companies),
        ("mart_jobs_over_time", sample.jobs_over_time),
    ):
        assert not frame.empty, name
        assert dd._REQUIRED_COLUMNS[name] <= set(frame.columns), name
    assert str(sample.skills["published_at"].dtype).startswith("datetime64")
    assert sample.skills["published_at"].dt.tz is not None
    assert str(sample.jobs_over_time["week_start"].dtype).startswith("datetime64")
    assert pd.api.types.is_numeric_dtype(sample.salary_ranges["avg_salary_usd"])


def test_sample_salary_counts_add_up_across_levels(sample: dd.DashboardData):
    """source-level job_count must equal the sum of its salary_source split."""
    ranges = sample.salary_ranges
    by_source = ranges[ranges["grouping_level"] == "source"]
    split = ranges[ranges["grouping_level"] == "source_salary_source"]
    for row in by_source.itertuples():
        assert row.job_count == split[split["source"] == row.source]["job_count"].sum()


def test_sample_skills_are_normalized(sample: dd.DashboardData):
    skills = sample.skills["skill"]
    assert (skills == skills.str.strip().str.lower()).all()
    assert set(sample.skills["skill_type"].unique()) <= {"tag", "category"}


def test_falls_back_to_demo_without_database_url():
    bundle = dd.load_dashboard_data(None)
    assert bundle.is_demo
    assert "not configured" in bundle.demo_reason


def test_falls_back_to_demo_when_warehouse_fails():
    def boom(database_url: str, schema: str | None = None) -> dd.DashboardData:
        raise RuntimeError("connection refused")

    bundle = dd.load_dashboard_data("postgresql://user:pw@host/db", warehouse_loader=boom)
    assert bundle.is_demo
    assert "RuntimeError" in bundle.demo_reason


def test_uses_warehouse_data_when_available(sample: dd.DashboardData):
    live = dd.DashboardData(
        skills=sample.skills,
        salary_ranges=sample.salary_ranges,
        companies=sample.companies,
        jobs_over_time=sample.jobs_over_time,
        is_demo=False,
    )

    bundle = dd.load_dashboard_data(
        "postgresql://user:pw@host/db", warehouse_loader=lambda url, schema=None: live
    )
    assert bundle is live
    assert not bundle.is_demo


def test_to_sqlalchemy_url_targets_psycopg3():
    assert dd.to_sqlalchemy_url("postgresql://u:p@h/db") == "postgresql+psycopg://u:p@h/db"
    assert dd.to_sqlalchemy_url("postgres://u:p@h/db") == "postgresql+psycopg://u:p@h/db"
    assert dd.to_sqlalchemy_url("postgresql+psycopg://u@h/db") == "postgresql+psycopg://u@h/db"


# --- top_skills --------------------------------------------------------------


@pytest.fixture()
def tiny_skills() -> pd.DataFrame:
    # remotive:1 mentions python twice (tag AND category): must count once.
    return pd.DataFrame(
        {
            "job_key": ["remotive:1", "remotive:1", "remotive:2", "remoteok:3"],
            "source": ["remotive", "remotive", "remotive", "remoteok"],
            "skill": ["python", "python", "python", "aws"],
            "skill_type": ["tag", "category", "tag", "tag"],
            "published_at": pd.to_datetime(["2026-07-01"] * 4, utc=True),
        }
    )


def test_top_skills_counts_each_job_once_per_skill(tiny_skills: pd.DataFrame):
    ranked = dd.top_skills(tiny_skills)
    assert ranked.loc[ranked["skill"] == "python", "job_count"].item() == 2
    assert ranked.loc[ranked["skill"] == "aws", "job_count"].item() == 1
    assert ranked["job_count"].is_monotonic_decreasing


def test_top_skills_applies_filters_and_limit(tiny_skills: pd.DataFrame):
    only_remoteok = dd.top_skills(tiny_skills, sources=["remoteok"])
    assert only_remoteok["skill"].tolist() == ["aws"]

    only_categories = dd.top_skills(tiny_skills, skill_types=["category"])
    assert only_categories["skill"].tolist() == ["python"]
    assert only_categories["job_count"].item() == 1

    assert len(dd.top_skills(tiny_skills, limit=1)) == 1


# --- salary_breakdown ---------------------------------------------------------


def test_salary_breakdown_returns_one_level_verbatim(sample: dd.DashboardData):
    rows = dd.salary_breakdown(sample.salary_ranges, "source")
    assert set(rows["grouping_level"].unique()) == {"source"}
    # no re-aggregation: values must match the mart row exactly
    mart = sample.salary_ranges
    for row in rows.itertuples():
        original = mart[(mart["grouping_level"] == "source") & (mart["source"] == row.source)]
        assert row.avg_salary_usd == original["avg_salary_usd"].item()
        assert row.job_count == original["job_count"].item()
    assert rows["avg_salary_usd"].is_monotonic_decreasing


def test_salary_breakdown_labels_confidence(sample: dd.DashboardData):
    split = dd.salary_breakdown(sample.salary_ranges, "source_salary_source")
    assert set(split["confidence_label"]) <= set(dd.SALARY_SOURCE_LABELS.values())
    assert split["group_label"].str.contains("·").all()

    merged = dd.salary_breakdown(sample.salary_ranges, "source")
    assert (merged["confidence_label"] == dd.MIXED_CONFIDENCE_LABEL).all()


def test_salary_breakdown_filters_sources(sample: dd.DashboardData):
    rows = dd.salary_breakdown(sample.salary_ranges, "source", sources=["remoteok"])
    assert set(rows["source"].unique()) == {"remoteok"}


def test_salary_breakdown_rejects_unknown_level(sample: dd.DashboardData):
    with pytest.raises(ValueError, match="grouping_level"):
        dd.salary_breakdown(sample.salary_ranges, "per_company")


# --- jobs_over_time_series ----------------------------------------------------


def test_jobs_over_time_by_source(sample: dd.DashboardData):
    series = dd.jobs_over_time_series(sample.jobs_over_time, "source")
    assert list(series.columns) == ["week_start", "series", "job_count"]
    assert set(series["series"].unique()) == {"adzuna", "remoteok", "remotive"}

    only = dd.jobs_over_time_series(sample.jobs_over_time, "source", sources=["adzuna"])
    assert set(only["series"].unique()) == {"adzuna"}


def test_jobs_over_time_sums_skill_counts_across_sources():
    week = pd.Timestamp("2026-07-06")
    frame = pd.DataFrame(
        {
            "grouping_level": ["source_skill"] * 3 + ["source"],
            "week_start": [week] * 4,
            "source": ["remotive", "remoteok", "remoteok", "remotive"],
            "skill": ["python", "python", "aws", None],
            "job_count": [3, 2, 5, 30],
        }
    )
    series = dd.jobs_over_time_series(frame, "source_skill", skills=["python"])
    assert len(series) == 1
    assert series["job_count"].item() == 5  # 3 + 2, source rows excluded

    scoped = dd.jobs_over_time_series(
        frame, "source_skill", sources=["remoteok"], skills=["python"]
    )
    assert scoped["job_count"].item() == 2


def test_jobs_over_time_rejects_unknown_level(sample: dd.DashboardData):
    with pytest.raises(ValueError, match="grouping_level"):
        dd.jobs_over_time_series(sample.jobs_over_time, "daily")


# --- companies and skill picker ----------------------------------------------


def test_top_companies_sorted_and_limited(sample: dd.DashboardData):
    ranked = dd.top_companies(sample.companies, limit=10)
    assert len(ranked) == 10
    assert ranked["job_count"].is_monotonic_decreasing


def test_top_time_skills_ranked_by_volume(sample: dd.DashboardData):
    picks = dd.top_time_skills(sample.jobs_over_time, limit=5)
    assert len(picks) == 5
    rows = sample.jobs_over_time
    totals = (
        rows[rows["grouping_level"] == "source_skill"].groupby("skill")["job_count"].sum()
    )
    assert picks[0] == totals.idxmax()
    assert totals[picks].is_monotonic_decreasing


def test_category_only_skills_excluded_from_skill_picker(sample: dd.DashboardData):
    board_labels = dd.category_only_skills(sample.skills)
    # broad board categories exist in the sample and are not real tags
    assert "it jobs" in board_labels
    assert "python" not in board_labels

    picks = dd.top_time_skills(sample.jobs_over_time, limit=8, exclude=board_labels)
    assert not board_labels & set(picks)
