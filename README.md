# RemoteRadar

Python ETL pipeline that collects remote tech job postings from public APIs,
cleans and transforms the data, loads it into a PostgreSQL data warehouse and —
in upcoming phases — powers a trends dashboard: most requested languages,
salary ranges, companies hiring remote the most, and evolution over time.

> **Phased project.** This is Phase 3 of 7: three extraction sources
> (Remotive, RemoteOK, Adzuna) landing raw JSONB payloads in PostgreSQL, a
> pipeline CLI that orchestrates them with partial-failure tolerance, and a
> dbt project with staging models plus an analytics layer — currency-
> normalized jobs, skills, salary ranges, top remote companies and postings
> over time. Orchestration with Prefect, data quality checks and the
> dashboard arrive in the next phases.

## Planned stack

| Layer               | Tool                                            |
| ------------------- | ----------------------------------------------- |
| Extraction          | Python + [httpx](https://www.python-httpx.org/) |
| Orchestration       | Prefect (open-source)                           |
| Transformation      | dbt-core                                        |
| Warehouse           | PostgreSQL (Supabase or Railway free tier)      |
| Data quality        | Great Expectations                              |
| Dashboard           | Streamlit (Streamlit Community Cloud)           |
| Testing             | Pytest                                          |
| CI / Scheduling     | GitHub Actions (daily cron)                     |

## Current structure

```
remoteradar/
├── src/remoteradar/
│   ├── config.py            # Environment variable handling (.env)
│   ├── load.py              # Raw payload loading into PostgreSQL
│   ├── pipeline.py          # CLI orchestrating all extractions
│   └── extract/
│       ├── remotive.py      # Remotive API extraction
│       ├── remoteok.py      # RemoteOK API extraction
│       └── adzuna.py        # Adzuna API extraction
├── sql/                     # Landing table DDL (one file per source)
├── transform/               # dbt project (staging + analytics layers)
│   ├── dbt_project.yml
│   ├── profiles.yml         # Connection via DBT_PG_* env vars (no secrets)
│   ├── seeds/               # exchange_rates.csv (fixed FX snapshot, see limitations)
│   ├── tests/generic/       # Custom generic tests (min_not_greater_than_max)
│   └── models/
│       ├── staging/         # stg_remotive_jobs, stg_remoteok_jobs, stg_adzuna_jobs
│       ├── intermediate/    # int_jobs_normalized (all sources unified, salaries in USD)
│       └── marts/           # mart_skills, mart_salary_ranges, mart_companies, mart_jobs_over_time
├── tests/                   # Tests with mocked HTTP (never hits the real APIs)
├── .env.example             # Documented environment variables
└── pyproject.toml           # Dependencies and tool configuration
```

## Data sources

| Source                                       | Endpoint                                  | Tech filter                                                    | Auth                          |
| -------------------------------------------- | ----------------------------------------- | -------------------------------------------------------------- | ----------------------------- |
| [Remotive](https://remotive.com/api/remote-jobs) | `GET /api/remote-jobs?category=<slug>` | One call per tech category + client-side filter and dedup¹     | None                          |
| [RemoteOK](https://remoteok.com/api)         | `GET /api?tags=<tag>`                     | One call per tech tag + client-side filter and dedup²          | None                          |
| [Adzuna](https://developer.adzuna.com/)      | `GET /v1/api/jobs/{country}/search/{page}` | Server-side `category=it-jobs`, paged, dedup by id            | `ADZUNA_APP_ID` / `ADZUNA_APP_KEY` |

¹ The Remotive public API currently ignores the `category` parameter
server-side, so responses are filtered client-side by category name.
² RemoteOK honours `?tags=` only loosely and caps responses at ~100 jobs, so
the extraction requests several tags and filters/deduplicates client-side.

Each extraction consolidates its calls into a single payload
(`{"job-count", "jobs", ...}` plus fields recording which
categories/tags/pages succeeded or failed) and stores it as one JSONB row in
the corresponding `raw.*` table.

## Running locally

Requires Python 3.11+.

```bash
# 1. Create and activate the virtual environment
python -m venv .venv
.venv\Scripts\activate        # Windows
# source .venv/bin/activate   # Linux/macOS

# 2. Install the project with development dependencies
pip install -e ".[dev]"

# 3. Configure environment variables
# Copy .env.example to .env and fill in the values (see table below)
```

### Environment variables

| Variable           | Required     | Description                                                              |
| ------------------ | ------------ | ------------------------------------------------------------------------ |
| `DATABASE_URL`     | Yes (load)   | PostgreSQL connection string (`postgresql://user:pass@host:5432/db`)     |
| `ADZUNA_APP_ID`    | Yes (Adzuna) | Adzuna application id — register free at <https://developer.adzuna.com/> |
| `ADZUNA_APP_KEY`   | Yes (Adzuna) | Adzuna application key                                                   |
| `ADZUNA_COUNTRY`   | No           | Country for Adzuna searches (default: `gb`, Adzuna's deepest market)     |
| `REMOTIVE_API_URL` | No           | Remotive API base URL (default: public endpoint)                         |
| `REMOTEOK_API_URL` | No           | RemoteOK API base URL (default: public endpoint)                         |
| `ADZUNA_API_URL`   | No           | Adzuna API base URL (default: public endpoint)                           |
| `DBT_PG_*`         | Yes (dbt)    | Warehouse connection for dbt — see the dbt section below                 |

### Create the raw tables in PostgreSQL

```bash
psql "$DATABASE_URL" -f sql/001_create_raw_remotive_jobs.sql
psql "$DATABASE_URL" -f sql/002_create_raw_remoteok_jobs.sql
psql "$DATABASE_URL" -f sql/003_create_raw_adzuna_jobs.sql
```

### Run the pipeline

```bash
python -m remoteradar.pipeline   # or simply: remoteradar
```

Runs the three extractions in sequence and loads each consolidated payload
into its `raw.*` landing table. Failures are tolerated at two levels:

- **Inside a source** — a failing Remotive category, RemoteOK tag or Adzuna
  page is logged and recorded in the stored payload, and the extraction
  continues with the rest.
- **Across sources** — if a whole source fails (API down, bad credentials),
  the error is logged and the pipeline continues with the remaining sources.
  The process only fails if *all* sources fail.

This is the entry point Prefect (Phase 5) and GitHub Actions (Phase 7) will
call. Each source can also be run on its own:

```bash
python -m remoteradar.extract.remotive
python -m remoteradar.extract.remoteok
python -m remoteradar.extract.adzuna
```

## Transformations (dbt)

The dbt project lives in [`transform/`](transform/) and is organized in three
layers:

- **Staging** (`models/staging/`, views) — defines the three raw tables as
  sources and parses each JSONB payload into one row per job with normalized
  columns (`job_id`, `title`, `company`, `category`/`tags`, `location`,
  `salary_min`/`salary_max`/`salary_text`, `published_at`, `url`, `source`),
  deduplicated to the latest ingestion of each job.
- **Intermediate** (`models/intermediate/`, views) —
  `int_jobs_normalized` unions the three staging models into one row per job
  across all sources and normalizes salaries to USD: RemoteOK values pass
  through (already USD), Adzuna values are converted from the search
  country's currency using the fixed rates in
  [`seeds/exchange_rates.csv`](transform/seeds/exchange_rates.csv), and
  Remotive keeps only its free-text salary. A `salary_source` column
  (`structured_usd` / `structured_converted` / `text_only` / `missing`)
  labels how trustworthy each job's USD values are, so the dashboard can
  filter or warn.
- **Marts** (`models/marts/`, tables) — the dashboard-facing layer:
  - `mart_skills` — one row per job per skill, exploding tag arrays
    (RemoteOK, Remotive) and category labels (Remotive, Adzuna).
  - `mart_salary_ranges` — USD salary statistics (count, average, median,
    min, max) by source, by salary confidence and by category, each grain
    labelled by a `grouping_level` column.
  - `mart_companies` — job counts per company across all sources.
  - `mart_jobs_over_time` — weekly posting counts by source and by
    source/skill, bucketed by `published_at` (it carries real history even
    while the pipeline is young; `ingested_at` would only mirror our own run
    schedule).

Every model's columns, origins and tests are documented in the `schema.yml`
next to it. Data tests cover not-null/unique keys, accepted values for the
label columns and a custom generic test ensuring `salary_min` never exceeds
`salary_max`.

`transform/profiles.yml` is committed (it contains no secrets): the
connection comes from `DBT_PG_HOST`, `DBT_PG_PORT`, `DBT_PG_USER`,
`DBT_PG_PASSWORD`, `DBT_PG_DBNAME` and `DBT_PG_SCHEMA` — the same values as
`DATABASE_URL`, split into components, since dbt-postgres does not accept a
connection string. **dbt does not read `.env` files**: export these variables
in your shell before running dbt.

```bash
cd transform
dbt debug                  # checks connection and project setup
dbt seed                   # loads seeds/exchange_rates.csv (needed once, and after edits)
dbt run                    # builds all layers (staging + intermediate + marts)
dbt run --select marts     # or rebuild only the marts
dbt test                   # runs the data tests from the schema.yml files
```

`dbt seed`/`dbt run`/`dbt test` require a reachable PostgreSQL with the raw
tables created; `dbt parse` validates the project without a database.

### Known limitations

- **Fixed exchange rates.** Adzuna salaries are converted to USD with the
  static snapshot in
  [`seeds/exchange_rates.csv`](transform/seeds/exchange_rates.csv), captured
  on **2026-07-15** (the `captured_at` column records the date). The project
  is 100% free tier and deliberately avoids calling an FX API from inside
  dbt, so these rates drift over time. They are fine for trend analysis and
  portfolio purposes but must **not** be used for real financial decisions.
  Jobs whose salary went through this conversion are labelled
  `salary_source = 'structured_converted'`.
- **Salary coverage is uneven across sources.** Remotive only publishes
  free-text salaries (kept in `salary_text`, not parsed yet), so its jobs
  never contribute to the USD salary marts; Adzuna salaries may be
  model-predicted (`salary_is_predicted`).
- **No company name reconciliation.** Companies are grouped by
  lowercased/trimmed name only — the same employer spelled differently
  across sources ("Acme" vs "Acme Inc") counts as separate rows in
  `mart_companies`. Fuzzy matching may come in a later phase.
- **Skill granularity differs per source.** Adzuna has no free-form tags,
  only a single broad category ("IT Jobs"), so skill-level charts should
  filter `skill_type = 'tag'` (RemoteOK and Remotive).
- **Shallow history.** Until the pipeline runs on a schedule (Phase 7),
  `mart_jobs_over_time` only covers the weeks the sources happen to list —
  the structure matters more than the volume for now.

### Tests and lint

```bash
pytest
ruff check .
```

## License

[MIT](LICENSE)
