"""RemoteRadar dashboard — Streamlit UI layer.

Run locally from the repository root (same working directory Streamlit
Community Cloud uses):

    streamlit run dashboard/app.py

This module only renders: every query/shaping decision lives in
``dashboard.data`` so it can be tested without Streamlit.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

# Community Cloud executes this file from the repo root; locally it may be run
# from anywhere. Make the repo root importable so ``dashboard.data`` resolves.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from dashboard import data as dd  # noqa: E402

try:  # optional: local convenience, mirrors the pipeline's .env support
    from dotenv import load_dotenv

    load_dotenv()
except ModuleNotFoundError:
    pass

# Categorical palette (validated, fixed slot order — see the repo README's
# dashboard section). Colors follow the entity: a source keeps its hue no
# matter which filters are active.
_LIGHT = {
    "slots": [
        "#2a78d6", "#008300", "#e87ba4", "#eda100",
        "#1baf7a", "#eb6834", "#4a3aa7", "#e34948",
    ],
    "accent": "#2a78d6",
    "range_rule": "#c3c2b7",
}
_DARK = {
    "slots": [
        "#3987e5", "#008300", "#d55181", "#c98500",
        "#199e70", "#d95926", "#9085e9", "#e66767",
    ],
    "accent": "#3987e5",
    "range_rule": "#52514e",
}

SOURCE_ORDER = ["remotive", "remoteok", "adzuna"]
CONFIDENCE_ORDER = [
    dd.SALARY_SOURCE_LABELS["structured_usd"],
    dd.SALARY_SOURCE_LABELS["structured_converted"],
]


def _palette() -> dict:
    """Theme-aware palette: dark steps of the same hues on a dark surface."""
    try:
        theme_type = st.context.theme.type
    except Exception:  # older Streamlit or no browser context
        theme_type = "light"
    return _DARK if theme_type == "dark" else _LIGHT


@st.cache_data(ttl=600, show_spinner="Loading mart data…")
def _load(database_url: str | None, schema: str | None) -> dd.DashboardData:
    return dd.load_dashboard_data(database_url, schema)


def _bar_chart(frame: pd.DataFrame, *, y: str, x: str, color: str, tooltip: list) -> alt.Chart:
    """Horizontal magnitude bar: single hue, thin marks, recessive axes."""
    return (
        alt.Chart(frame)
        .mark_bar(color=color, cornerRadiusEnd=4, height={"band": 0.7})
        .encode(
            y=alt.Y(f"{y}:N", sort="-x", title=None),
            x=alt.X(f"{x}:Q", title="job postings", axis=alt.Axis(tickMinStep=1)),
            tooltip=tooltip,
        )
        .properties(height=max(180, 26 * len(frame)))
    )


def _table_view(frame: pd.DataFrame) -> None:
    with st.expander("View as table"):
        st.dataframe(frame, width="stretch", hide_index=True)


def render_header(bundle: dd.DashboardData) -> None:
    st.title("📡 RemoteRadar")
    st.caption(
        "Remote tech job market from three public job boards — Remotive, Remote OK "
        "and Adzuna — extracted, warehoused and modelled by the RemoteRadar pipeline."
    )
    if bundle.is_demo:
        st.warning(
            "**Demo mode** — showing bundled *sample* data "
            f"({bundle.demo_reason}). All numbers below are synthetic and "
            "illustrate the dashboard, not the real job market.",
            icon="🧪",
        )
    else:
        st.success("Live mode — reading the dbt marts from the warehouse.", icon="✅")

    postings = int(
        bundle.jobs_over_time.loc[
            bundle.jobs_over_time["grouping_level"] == "source", "job_count"
        ].sum()
    )
    weeks = bundle.jobs_over_time.loc[
        bundle.jobs_over_time["grouping_level"] == "source", "week_start"
    ].nunique()
    tiles = st.columns(4)
    tiles[0].metric("Postings tracked", f"{postings:,}")
    tiles[1].metric("Companies", f"{len(bundle.companies):,}")
    tiles[2].metric("Distinct skills", f"{bundle.skills['skill'].nunique():,}")
    tiles[3].metric("Weeks covered", f"{weeks}")


def render_skills(bundle: dd.DashboardData, sources: list[str]) -> None:
    st.subheader("Most requested skills")
    controls = st.columns([2, 1])
    kind = controls[0].radio(
        "Skill signal",
        options=["Tags", "Categories", "Both"],
        horizontal=True,
        help=(
            "Tags are free-form skills from Remote OK and Remotive. Categories are "
            "broad board labels (Remotive categories; Adzuna only publishes "
            "“IT Jobs”), kept separate so they don't drown out real skills."
        ),
    )
    limit = controls[1].slider("Top N", min_value=5, max_value=30, value=15, step=5)
    skill_types = {"Tags": ["tag"], "Categories": ["category"], "Both": None}[kind]
    ranked = dd.top_skills(bundle.skills, sources=sources, skill_types=skill_types, limit=limit)
    if ranked.empty:
        st.info("No skills match the current filters.")
        return
    st.altair_chart(
        _bar_chart(
            ranked,
            y="skill",
            x="job_count",
            color=_palette()["accent"],
            tooltip=[
                alt.Tooltip("skill:N", title="skill"),
                alt.Tooltip("job_count:Q", title="postings", format=","),
            ],
        ),
        width="stretch",
    )
    _table_view(ranked)


def render_salaries(bundle: dd.DashboardData, sources: list[str]) -> None:
    st.subheader("Salary ranges (USD)")
    level_labels = {
        "source": "By source",
        "source_salary_source": "By source × confidence",
        "source_category": "By source × category",
    }
    level = st.radio(
        "Aggregation grain",
        options=list(level_labels),
        format_func=level_labels.get,
        horizontal=True,
    )
    st.caption(
        f"Showing `grouping_level = {level}` exactly as pre-aggregated by dbt — "
        "the dashboard never re-averages these rows."
    )
    rows = dd.salary_breakdown(bundle.salary_ranges, level, sources=sources)
    if rows.empty:
        st.info("No salary rows match the current filters.")
        return

    palette = _palette()
    base = alt.Chart(rows).encode(y=alt.Y("group_label:N", sort="-x", title=None))
    span = base.mark_rule(color=palette["range_rule"], strokeWidth=2).encode(
        x=alt.X(
            "min_salary_usd:Q",
            title="USD / year",
            axis=alt.Axis(format="$~s"),
            scale=alt.Scale(zero=False),
        ),
        x2="max_salary_usd:Q",
    )
    tooltip = [
        alt.Tooltip("group_label:N", title="group"),
        alt.Tooltip("confidence_label:N", title="confidence"),
        alt.Tooltip("job_count:Q", title="postings", format=","),
        alt.Tooltip("avg_salary_usd:Q", title="average", format="$,.0f"),
        alt.Tooltip("median_salary_usd:Q", title="median", format="$,.0f"),
        alt.Tooltip("min_salary_usd:Q", title="range low", format="$,.0f"),
        alt.Tooltip("max_salary_usd:Q", title="range high", format="$,.0f"),
    ]
    if level == "source_salary_source":
        avg_color = alt.Color(
            "confidence_label:N",
            title="salary confidence",
            scale=alt.Scale(domain=CONFIDENCE_ORDER, range=palette["slots"][:2]),
            legend=alt.Legend(orient="bottom"),
        )
        avg = base.mark_circle(size=110, opacity=1).encode(
            x="avg_salary_usd:Q", color=avg_color, tooltip=tooltip
        )
    else:
        avg = base.mark_circle(size=110, opacity=1, color=palette["accent"]).encode(
            x="avg_salary_usd:Q", tooltip=tooltip
        )
    median = base.mark_tick(color=palette["range_rule"], thickness=2, size=18).encode(
        x="median_salary_usd:Q", tooltip=tooltip
    )
    st.altair_chart(
        (span + median + avg).properties(height=max(160, 44 * len(rows))),
        width="stretch",
    )
    st.caption(
        "Line = posted min–max range · ● = average · ┃ = median, over the posted "
        "midpoints. Only postings with structured salary data appear here — "
        "Remotive publishes free-text salaries only, so it has no rows. Values "
        "marked **≈** were converted to USD with a fixed approximate exchange "
        "rate (documented limitation of the fixed-rate seed)."
    )
    _table_view(
        rows[
            [
                "group_label",
                "confidence_label",
                "job_count",
                "avg_salary_usd",
                "median_salary_usd",
                "min_salary_usd",
                "max_salary_usd",
            ]
        ]
    )


def render_companies(bundle: dd.DashboardData) -> None:
    st.subheader("Companies hiring remote the most")
    limit = st.slider("Top N companies", min_value=5, max_value=30, value=15, step=5)
    ranked = dd.top_companies(bundle.companies, limit=limit)
    if ranked.empty:
        st.info("No company rows available.")
        return
    st.altair_chart(
        _bar_chart(
            ranked,
            y="company",
            x="job_count",
            color=_palette()["accent"],
            tooltip=[
                alt.Tooltip("company:N", title="company"),
                alt.Tooltip("job_count:Q", title="postings", format=","),
                alt.Tooltip("sources:N", title="seen on"),
            ],
        ),
        width="stretch",
    )
    st.caption(
        "Counts span all sources a company appears on, so the source filter does "
        "not apply here. Names are matched case-insensitively only — the same "
        "employer spelled differently counts separately (known limitation)."
    )
    _table_view(ranked[["company", "job_count", "source_count", "sources"]])


def render_over_time(bundle: dd.DashboardData, sources: list[str]) -> None:
    st.subheader("Postings over time")
    palette = _palette()
    mode = st.radio(
        "Break down by",
        options=["Source", "Skill"],
        horizontal=True,
    )
    if mode == "Source":
        level = "source"
        series = dd.jobs_over_time_series(bundle.jobs_over_time, level, sources=sources)
        domain = [s for s in SOURCE_ORDER if s in series["series"].unique()]
        color_range = [palette["slots"][SOURCE_ORDER.index(s)] for s in domain]
    else:
        level = "source_skill"
        skill_options = dd.top_time_skills(
            bundle.jobs_over_time,
            limit=8,
            exclude=dd.category_only_skills(bundle.skills),
        )
        picked = st.multiselect(
            "Skills (top 8 by volume; broad board categories excluded)",
            options=skill_options,
            default=skill_options[:4],
        )
        series = dd.jobs_over_time_series(
            bundle.jobs_over_time, level, sources=sources, skills=picked
        )
        # Fixed domain over the full option list: a skill keeps its color when
        # the selection changes (color follows the entity, not its rank).
        domain = skill_options
        color_range = palette["slots"][: len(domain)]
    st.caption(
        f"Showing `grouping_level = {level}` — weekly counts by `published_at`. "
        "While the pipeline is young this reflects each board's listing window, "
        "not full market history."
    )
    if series.empty:
        st.info("No rows match the current filters.")
        return
    chart = (
        alt.Chart(series)
        .mark_line(strokeWidth=2, point=alt.OverlayMarkDef(filled=True, size=64))
        .encode(
            x=alt.X("week_start:T", title=None, axis=alt.Axis(format="%d %b")),
            y=alt.Y("job_count:Q", title="postings / week", axis=alt.Axis(tickMinStep=1)),
            color=alt.Color(
                "series:N",
                title=None,
                scale=alt.Scale(domain=domain, range=color_range),
                legend=alt.Legend(orient="bottom"),
            ),
            tooltip=[
                alt.Tooltip("week_start:T", title="week of", format="%d %b %Y"),
                alt.Tooltip("series:N", title=mode.lower()),
                alt.Tooltip("job_count:Q", title="postings", format=","),
            ],
        )
        .properties(height=340)
    )
    st.altair_chart(chart, width="stretch")
    _table_view(series)


def render_footer() -> None:
    st.divider()
    # Remote OK's API terms require a visible mention plus a link back
    # (a normal link, no rel="nofollow") wherever its data is shown.
    st.markdown(
        "**Data sources:** [Remote OK](https://remoteok.com) · "
        "[Remotive](https://remotive.com) · [Adzuna](https://www.adzuna.com)  \n"
        "Job listings courtesy of **Remote OK**, Remotive and Adzuna. "
        "Salary figures are indicative only; converted values use a fixed "
        "approximate exchange rate."
    )
    st.caption(
        "Built with Streamlit · dbt · Prefect · Great Expectations — "
        "RemoteRadar, a portfolio ETL project."
    )


def main() -> None:
    st.set_page_config(page_title="RemoteRadar", page_icon="📡", layout="wide")
    bundle = _load(os.environ.get("DATABASE_URL"), os.environ.get("DBT_PG_SCHEMA"))

    render_header(bundle)

    with st.sidebar:
        st.header("Filters")
        sources = st.multiselect(
            "Sources",
            options=dd.source_options(bundle.skills),
            default=dd.source_options(bundle.skills),
            help="Applies to skills, salaries and the time series (not companies).",
        )
        st.caption(
            "Mode: **demo (sample data)**" if bundle.is_demo else "Mode: **live warehouse**"
        )

    render_skills(bundle, sources)
    st.divider()
    render_salaries(bundle, sources)
    st.divider()
    render_companies(bundle)
    st.divider()
    render_over_time(bundle, sources)
    render_footer()


main()
