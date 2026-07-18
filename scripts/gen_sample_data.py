"""Generate the sample mart CSVs that power the dashboard's demo mode.

Builds a synthetic jobs table shaped like int_jobs_normalized, then computes
the four marts with the same semantics as the dbt SQL, so the sample CSVs are
internally consistent (e.g. salary job_counts add up across grouping levels).

Deterministic: with SEED unchanged, the output is byte-identical to the CSVs
committed in dashboard/sample_data/. To regenerate them (e.g. after tweaking
the pools, sizes or distributions below), run from the repository root:

    python scripts/gen_sample_data.py
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import numpy as np
import pandas as pd

OUT_DIR = Path(__file__).resolve().parents[1] / "dashboard" / "sample_data"
SEED = 20260718
ISO_FMT = "%Y-%m-%dT%H:%M:%S+00:00"

rng = np.random.default_rng(SEED)

# --- pools -----------------------------------------------------------------

TAG_POOL = {
    "python": 30, "javascript": 26, "typescript": 22, "react": 24, "node.js": 16,
    "aws": 22, "docker": 16, "kubernetes": 14, "postgresql": 12, "sql": 14,
    "java": 12, "go": 10, "rust": 6, "php": 8, "ruby": 5, "c#": 7, ".net": 6,
    "machine learning": 9, "data engineering": 7, "data science": 8,
    "devops": 12, "terraform": 8, "linux": 9, "git": 8, "api": 10,
    "backend": 20, "frontend": 18, "full stack": 14, "mobile": 7, "ios": 5,
    "android": 5, "security": 6, "cloud": 10, "azure": 8, "gcp": 6,
    "senior": 18, "lead": 8, "vue": 6, "django": 7, "graphql": 6,
}

REMOTIVE_CATEGORIES = {
    "Software Development": 45, "Data": 12, "DevOps / Sysadmin": 10,
    "Design": 7, "Product": 7, "QA": 6, "Customer Service": 5,
    "Marketing": 4, "Sales / Business": 4,
}

COMPANIES_SHARED = [
    "GitLab", "Automattic", "Doist", "Zapier", "Buffer", "Toggl", "Hotjar",
    "Remote", "Deel", "Canonical",
]
COMPANIES_REMOTIVE = COMPANIES_SHARED + [
    "Mozilla", "DuckDuckGo", "Elastic", "Grafana Labs", "Aiven", "Timescale",
    "Netguru", "10up", "Close", "Help Scout", "Kinsta", "Clevertech",
    "X-Team", "Komoot", "Bitovi", "SketchDeck", "Paylocity", "Knack",
    "SafetyWing", "Float", "Podia", "Wikimedia Foundation",
]
COMPANIES_REMOTEOK = COMPANIES_SHARED + [
    "Stripe", "Shopify", "Coinbase", "Kraken", "Chainlink Labs", "Sourcegraph",
    "PostHog", "Supabase", "Render", "Fly.io", "Vercel", "Netlify",
    "DigitalOcean", "Docker", "HashiCorp", "Tailscale", "Ghost", "Basecamp",
]
COMPANIES_ADZUNA = COMPANIES_SHARED[:4] + [
    "Sky", "BT Group", "Capgemini", "Accenture", "Sainsbury's Tech",
    "Lloyds Banking Group", "Monzo", "Starling Bank", "Ocado Technology",
    "The Very Group", "BAE Systems Digital", "Softcat", "Kainos", "Endava",
    "AND Digital", "Zellis", "Registers of Scotland", "NHS Digital",
]

GBP_TO_USD = 1.27  # mirrors the fixed approximate rate in seeds/exchange_rates.csv

TODAY = dt.date(2026, 7, 17)


def weighted_choice(pool: dict[str, int], size: int) -> list[str]:
    keys = list(pool)
    weights = np.array([pool[k] for k in keys], dtype=float)
    weights /= weights.sum()
    return list(rng.choice(keys, size=size, replace=False, p=weights))


def random_published(days_back: int, size: int) -> list[pd.Timestamp]:
    """Datetimes within the last `days_back` days, skewed toward recent."""
    # beta(2, 1) skews toward 1.0 == most recent
    offsets = (1 - rng.beta(2.0, 1.2, size=size)) * days_back
    stamps = []
    for off in offsets:
        base = dt.datetime.combine(TODAY, dt.time()) - dt.timedelta(days=float(off))
        base += dt.timedelta(hours=int(rng.integers(6, 22)), minutes=int(rng.integers(0, 60)))
        stamps.append(pd.Timestamp(base, tz="UTC"))
    return stamps


def pick_company(pool: list[str], size: int) -> list[str]:
    # Zipf-ish: few companies post a lot
    ranks = np.arange(1, len(pool) + 1, dtype=float)
    weights = 1.0 / ranks**0.9
    weights /= weights.sum()
    shuffled = list(pool)
    rng.shuffle(shuffled)
    return list(rng.choice(shuffled, size=size, p=weights))


jobs: list[dict] = []

# --- remotive: categories + tags, salary text-only (never in salary mart) ---
for i in range(160):
    cat = weighted_choice(REMOTIVE_CATEGORIES, 1)[0]
    tags = weighted_choice(TAG_POOL, int(rng.integers(2, 7)))
    jobs.append(
        {
            "job_id": str(1_900_000 + int(rng.integers(0, 90_000))) + str(i),
            "source": "remotive",
            "company": None,  # filled below
            "category": cat,
            "tags": tags,
            "salary_min_usd": None,
            "salary_max_usd": None,
            "salary_source": "text_only" if rng.random() < 0.4 else "missing",
            "published_at": None,
        }
    )

# --- remoteok: tags only, USD salaries as posted -----------------------------
for i in range(120):
    has_salary = rng.random() < 0.75
    smin = smax = None
    if has_salary:
        smin = int(rng.integers(8, 25)) * 5000  # 40k..120k
        smax = smin + int(rng.integers(4, 17)) * 5000  # +20k..80k
        if rng.random() < 0.06:
            smax = None  # a few min-only postings
    jobs.append(
        {
            "job_id": str(1_090_000 + i * 7 + int(rng.integers(0, 6))),
            "source": "remoteok",
            "company": None,
            "category": None,
            "tags": weighted_choice(TAG_POOL, int(rng.integers(3, 9))),
            "salary_min_usd": smin,
            "salary_max_usd": smax,
            "salary_source": "structured_usd" if has_salary else "missing",
            "published_at": None,
        }
    )

# --- adzuna: single broad category, GBP salaries converted at a fixed rate ---
for i in range(90):
    has_salary = rng.random() < 0.85
    smin = smax = None
    if has_salary:
        gmin = int(rng.integers(128, 360)) * 250  # 32k..90k GBP
        gmax = int(gmin * (1.1 + rng.random() * 0.4))
        smin = round(gmin * GBP_TO_USD)
        smax = round(gmax * GBP_TO_USD)
    jobs.append(
        {
            "job_id": str(5_200_000_000 + i * 13 + int(rng.integers(0, 12))),
            "source": "adzuna",
            "company": None,
            "category": "IT Jobs",
            "tags": None,
            "salary_min_usd": smin,
            "salary_max_usd": smax,
            "salary_source": "structured_converted" if has_salary else "missing",
            "published_at": None,
        }
    )

frame = pd.DataFrame(jobs)
frame["job_key"] = frame["source"] + ":" + frame["job_id"]
assert frame["job_key"].is_unique

masks = {
    "remotive": frame["source"] == "remotive",
    "remoteok": frame["source"] == "remoteok",
    "adzuna": frame["source"] == "adzuna",
}
frame.loc[masks["remotive"], "company"] = pick_company(
    COMPANIES_REMOTIVE, int(masks["remotive"].sum())
)
frame.loc[masks["remoteok"], "company"] = pick_company(
    COMPANIES_REMOTEOK, int(masks["remoteok"].sum())
)
frame.loc[masks["adzuna"], "company"] = pick_company(COMPANIES_ADZUNA, int(masks["adzuna"].sum()))
# listing windows differ per source: remotive keeps a longer tail
frame.loc[masks["remotive"], "published_at"] = random_published(84, int(masks["remotive"].sum()))
frame.loc[masks["remoteok"], "published_at"] = random_published(35, int(masks["remoteok"].sum()))
frame.loc[masks["adzuna"], "published_at"] = random_published(42, int(masks["adzuna"].sum()))
frame["published_at"] = pd.to_datetime(frame["published_at"], utc=True)

# --- mart_skills ------------------------------------------------------------
skill_rows = []
for row in frame.itertuples():
    if isinstance(row.tags, list):
        for tag in row.tags:
            skill_rows.append(
                (row.job_key, row.source, tag.lower().strip(), "tag", row.published_at)
            )
    if isinstance(row.category, str) and row.category:
        skill_rows.append(
            (row.job_key, row.source, row.category.lower().strip(), "category", row.published_at)
        )
mart_skills = (
    pd.DataFrame(skill_rows, columns=["job_key", "source", "skill", "skill_type", "published_at"])
    .drop_duplicates()
    .sort_values(["source", "job_key", "skill_type", "skill"])
    .reset_index(drop=True)
)

# --- mart_salary_ranges -----------------------------------------------------
salaried = frame[frame["salary_source"].isin(["structured_usd", "structured_converted"])].copy()
salaried["salary_mid_usd"] = (
    (salaried["salary_min_usd"] + salaried["salary_max_usd"]) / 2.0
).fillna(salaried["salary_min_usd"]).fillna(salaried["salary_max_usd"])


def salary_stats(group: pd.DataFrame) -> dict:
    return {
        "job_count": len(group),
        "avg_salary_usd": round(group["salary_mid_usd"].mean()),
        "median_salary_usd": round(group["salary_mid_usd"].median()),
        "min_salary_usd": int(group["salary_min_usd"].min()),
        "max_salary_usd": int(group["salary_max_usd"].max()),
    }


salary_levels = []
for source, group in salaried.groupby("source"):
    salary_levels.append(
        {"grouping_level": "source", "source": source, "salary_source": None, "category": None}
        | salary_stats(group)
    )
for (source, ssource), group in salaried.groupby(["source", "salary_source"]):
    salary_levels.append(
        {
            "grouping_level": "source_salary_source",
            "source": source,
            "salary_source": ssource,
            "category": None,
        }
        | salary_stats(group)
    )
salaried_cat = salaried.dropna(subset=["category"])
for (source, category), group in salaried_cat.groupby(["source", "category"]):
    salary_levels.append(
        {
            "grouping_level": "source_category",
            "source": source,
            "salary_source": None,
            "category": category,
        }
        | salary_stats(group)
    )
mart_salary_ranges = pd.DataFrame(salary_levels)

# --- mart_companies ---------------------------------------------------------
comp = frame.copy()
comp["company_key"] = comp["company"].str.strip().str.lower()
mart_companies = (
    comp.groupby("company_key")
    .agg(
        company=("company", lambda s: s.str.strip().max()),
        job_count=("job_key", "size"),
        source_count=("source", "nunique"),
        sources=("source", lambda s: ", ".join(sorted(s.unique()))),
        first_published_at=("published_at", "min"),
        last_published_at=("published_at", "max"),
    )
    .reset_index()
    .sort_values(["job_count", "company_key"], ascending=[False, True])
    .reset_index(drop=True)
)


# --- mart_jobs_over_time ----------------------------------------------------
def week_start(stamps: pd.Series) -> pd.Series:
    dates = stamps.dt.tz_convert("UTC").dt.normalize()
    return (dates - pd.to_timedelta(dates.dt.weekday, unit="D")).dt.date


by_source = frame.assign(week_start=week_start(frame["published_at"]))
time_source = (
    by_source.groupby(["week_start", "source"])
    .size()
    .rename("job_count")
    .reset_index()
    .assign(grouping_level="source", skill=None)
)
by_skill = mart_skills.assign(week_start=week_start(mart_skills["published_at"]))
time_skill = (
    by_skill.groupby(["week_start", "source", "skill"])
    .size()
    .rename("job_count")
    .reset_index()
    .assign(grouping_level="source_skill")
)
mart_jobs_over_time = (
    pd.concat([time_source, time_skill], ignore_index=True)[
        ["grouping_level", "week_start", "source", "skill", "job_count"]
    ]
    .sort_values(["grouping_level", "week_start", "source", "skill"])
    .reset_index(drop=True)
)

# --- coherence checks -------------------------------------------------------
lvl_source = mart_salary_ranges[mart_salary_ranges["grouping_level"] == "source"]
lvl_ss = mart_salary_ranges[mart_salary_ranges["grouping_level"] == "source_salary_source"]
for row in lvl_source.itertuples():
    assert row.job_count == lvl_ss[lvl_ss["source"] == row.source]["job_count"].sum(), row.source
totals = time_source.groupby("source")["job_count"].sum()
for source, mask in masks.items():
    assert totals[source] == mask.sum(), source
assert (mart_companies["job_count"].sum()) == len(frame)
assert set(mart_skills["source"].unique()) == {"remotive", "remoteok", "adzuna"}

# --- write ------------------------------------------------------------------
OUT_DIR.mkdir(parents=True, exist_ok=True)
mart_skills.assign(published_at=mart_skills["published_at"].dt.strftime(ISO_FMT)).to_csv(
    OUT_DIR / "mart_skills.csv", index=False
)
mart_salary_ranges.to_csv(OUT_DIR / "mart_salary_ranges.csv", index=False)
mart_companies.assign(
    first_published_at=mart_companies["first_published_at"].dt.strftime(ISO_FMT),
    last_published_at=mart_companies["last_published_at"].dt.strftime(ISO_FMT),
).to_csv(OUT_DIR / "mart_companies.csv", index=False)
mart_jobs_over_time.to_csv(OUT_DIR / "mart_jobs_over_time.csv", index=False)

print("mart_skills:", len(mart_skills))
print("mart_salary_ranges:", len(mart_salary_ranges))
print("mart_companies:", len(mart_companies))
print("mart_jobs_over_time:", len(mart_jobs_over_time))
