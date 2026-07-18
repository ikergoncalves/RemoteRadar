# RemoteRadar

Python ETL pipeline that collects remote tech job postings from public APIs,
cleans and transforms the data, loads it into a PostgreSQL data warehouse and —
in upcoming phases — powers a trends dashboard: most requested languages,
salary ranges, companies hiring remote the most, and evolution over time.

> **Phased project.** This is Phase 5 of 7: three extraction sources
> (Remotive, RemoteOK, Adzuna) landing raw JSONB payloads in PostgreSQL, a
> pipeline CLI that orchestrates them with partial-failure tolerance, a
> dbt project with staging models plus an analytics layer — currency-
> normalized jobs, skills, salary ranges, top remote companies and postings
> over time — a data quality layer with Great Expectations validating
> every warehouse layer, testable locally without a database, and a Prefect
> flow orchestrating the whole run (extract + load, dbt, validation) with a
> daily schedule ready for Prefect Cloud. The dashboard and CI arrive in the
> next phases.

## Planned stack

| Layer               | Tool                                            |
| ------------------- | ----------------------------------------------- |
| Extraction          | Python + [httpx](https://www.python-httpx.org/) |
| Orchestration       | Prefect 3 (Prefect Cloud free tier)             |
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
│   ├── pipeline.py          # CLI orchestrating all extractions (debug path)
│   ├── validate.py          # CLI running the quality suites against the warehouse
│   ├── orchestration/
│   │   └── flow.py          # Prefect flow: source tasks + dbt + validation, daily schedule
│   ├── quality/             # Great Expectations home: suites + check registry
│   │   ├── suites.py        # Expectation suites (raw, staging, marts), documented
│   │   └── checks.py        # Suite <-> warehouse table/query bindings
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
│   └── fixtures/            # Sample data (one table per layer) for the quality logic tests
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
| `DATABASE_URL`     | Yes (load, validate) | PostgreSQL connection string (`postgresql://user:pass@host:5432/db`) |
| `ADZUNA_APP_ID`    | Yes (Adzuna) | Adzuna application id — register free at <https://developer.adzuna.com/> |
| `ADZUNA_APP_KEY`   | Yes (Adzuna) | Adzuna application key                                                   |
| `ADZUNA_COUNTRY`   | No           | Country for Adzuna searches (default: `gb`, Adzuna's deepest market)     |
| `REMOTIVE_API_URL` | No           | Remotive API base URL (default: public endpoint)                         |
| `REMOTEOK_API_URL` | No           | RemoteOK API base URL (default: public endpoint)                         |
| `ADZUNA_API_URL`   | No           | Adzuna API base URL (default: public endpoint)                           |
| `DBT_PG_*`         | Yes (dbt)    | Warehouse connection for dbt — see the dbt section below (`DBT_PG_SCHEMA` is also read by `remoteradar-validate` to locate the dbt models) |

### Create the raw tables in PostgreSQL

```bash
psql "$DATABASE_URL" -f sql/001_create_raw_remotive_jobs.sql
psql "$DATABASE_URL" -f sql/002_create_raw_remoteok_jobs.sql
psql "$DATABASE_URL" -f sql/003_create_raw_adzuna_jobs.sql
```

### Run the full ETL (Prefect flow)

The primary way to run RemoteRadar is the Prefect flow, which chains
extraction + load, `dbt run` and the data quality gate in a single run:

```bash
python -m remoteradar.orchestration.flow   # or simply: remoteradar-flow
```

See [Orchestration (Prefect)](#orchestration-prefect) for the task
structure, the failure semantics, the daily schedule and the Prefect Cloud
setup steps.

### Run stages individually (debug)

Every stage the flow orchestrates is still runnable on its own — useful to
debug one piece without a Prefect run around it:

```bash
python -m remoteradar.pipeline           # extract + load all sources (or: remoteradar)
python -m remoteradar.extract.remotive   # a single extraction, no load
python -m remoteradar.extract.remoteok
python -m remoteradar.extract.adzuna
python -m remoteradar.validate           # quality checks (or: remoteradar-validate)
```

(dbt has its own commands — see [Transformations](#transformations-dbt).)

`python -m remoteradar.pipeline` runs the three extractions in sequence and
loads each consolidated payload into its `raw.*` landing table. Failures are
tolerated at two levels:

- **Inside a source** — a failing Remotive category, RemoteOK tag or Adzuna
  page is logged and recorded in the stored payload, and the extraction
  continues with the rest.
- **Across sources** — if a whole source fails (API down, bad credentials),
  the error is logged and the pipeline continues with the remaining sources.
  The process only fails if *all* sources fail.

The Prefect flow keeps exactly these semantics, adding retries, scheduling
and observability on top.

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

## Data quality (Great Expectations)

The quality layer lives in
[`src/remoteradar/quality/`](src/remoteradar/quality/) and uses
**Great Expectations Core 1.x** with the modern Fluent API: suites are
defined as Python code and executed against ephemeral Data Contexts.

**Layout decision.** There is deliberately no scaffolded `gx/` directory at
the repository root (the old `great_expectations init` workflow). A
file-based Data Context stores serialized YAML/JSON copies of the suites,
which would be a second source of truth drifting away from code review.
Defining the suites as code in `remoteradar.quality` keeps a single
reviewable definition that both consumers import: the warehouse validation
CLI and the local Pytest logic tests.

### Why a second validation layer next to the dbt tests

Some expectations intentionally overlap with the dbt tests in `transform/`
(e.g. `job_id` not-null/unique). This is not useless duplication:

- dbt tests validate models **while dbt builds them**, from inside the
  transformation tool. The GX suites validate the **materialized data from
  outside**, sharing no machinery with dbt — a broken dbt test, an edited
  `schema.yml` or a run that skipped tests cannot silently disable the
  quality gate.
- They run at different moments: dbt tests during `dbt test`, GX after the
  whole pipeline (and, in later phases, from Prefect and GitHub Actions
  before the dashboard reads the marts).
- GX also covers what dbt tests structurally cannot: the raw landing layer
  (payload/job-count coherence happens before dbt ever runs).

### Expectation suites

Each suite is built by a documented function in
[`quality/suites.py`](src/remoteradar/quality/suites.py);
[`quality/checks.py`](src/remoteradar/quality/checks.py) maps suites to
warehouse tables.

| Suite | Runs against | What it checks and why |
| ----- | ------------ | ---------------------- |
| `raw_jobs` | `raw.remotive_jobs`, `raw.remoteok_jobs`, `raw.adzuna_jobs` (via a SQL projection deriving job-count columns from the JSONB) | `payload` and `ingested_at` never null; `ingested_at` not in the future (5 min clock-skew tolerance) and not before the project existed — future/past values would corrupt the staging dedup-by-latest-ingestion; the payload's declared `job-count` equals the actual length of its `jobs` array — a mismatch means a truncated payload or an extractor regression. |
| `staging_jobs` | `stg_remotive_jobs`, `stg_remoteok_jobs`, `stg_adzuna_jobs` | `job_id` never null and unique within each view (cross-source collisions are handled by `job_key` downstream); `title` never null; `published_at`, when present, between 2015-01-01 and now+1 day — values outside that window mean timestamp-parsing drift, while nulls stay allowed (not every source dates every job). |
| `mart_salary_ranges` | `mart_salary_ranges` | `grouping_level` only takes its three documented grains (the dashboard filters on it); `job_count >= 1` (every row aggregates at least one job); `min_salary_usd <= max_salary_usd` when both bounds are present — the dbt `min_not_greater_than_max` test asserted on the materialized table. |
| `mart_companies` | `mart_companies` | `company_key` never null and unique (it is the mart's grouping key); `job_count >= 1`. |
| `mart_jobs_over_time` | `mart_jobs_over_time` | `grouping_level` only takes its two documented grains; `week_start` never null (jobs without `published_at` must have been filtered out); `job_count >= 1`. |

### Test the suites locally (no database needed)

The regular test run already does it:

```bash
pytest
```

[`tests/test_quality_suites.py`](tests/test_quality_suites.py) runs every
suite against small sample datasets in
[`tests/fixtures/`](tests/fixtures/) — one table per layer (raw payload
JSON, staging CSV, one CSV per mart) loaded as pandas DataFrames. Healthy
fixtures must pass their suite, and each targeted corruption (duplicate
ids, future timestamps, job-count mismatches, `min > max`, unknown
`grouping_level`…) must fail exactly the expectation that guards it.

> **Scope:** these tests validate the *logic* of the expectations, so the
> suites are trustworthy before any database exists. They are **not** an
> end-to-end validation of the warehouse — that requires the steps below
> and is still pending in this project (no real PostgreSQL has been
> provisioned yet).

### Validate the real warehouse

With `DATABASE_URL` set (and the dbt models built), run:

```bash
python -m remoteradar.validate   # or simply: remoteradar-validate
```

The CLI runs all nine checks (3 raw tables, 3 staging views, 3 marts) and
prints one line per check plus a failure detail per broken expectation:

```
== RemoteRadar data quality report ==
PASSED  raw_remotive_jobs (suite raw_jobs, 6/6 expectations)
FAILED  stg_adzuna_jobs (suite staging_jobs, 3/4 expectations)
        - expect_column_values_to_not_be_null on title: 12 unexpected value(s)
ERROR   mart_companies (suite mart_companies)
        relation "analytics.mart_companies" does not exist
Summary: 7 passed, 1 failed, 1 error(s)
```

The exit code is 0 only when every check passes, so orchestration (Prefect,
Phase 5) and CI (GitHub Actions, Phase 7) can call it as a gate after the
pipeline + dbt run. Notes:

- Checks are independent: a missing table (e.g. marts not built yet) is
  reported as `ERROR` and the remaining checks still run.
- The staging views and marts are read from the schema in `DBT_PG_SCHEMA`
  (default `analytics`), matching wherever dbt materialized them.
- Plain `postgresql://` connection strings are rewritten to SQLAlchemy's
  `postgresql+psycopg://` so validation uses the psycopg v3 driver the
  project already depends on (no psycopg2 needed).

## Orchestration (Prefect)

The orchestration layer lives in
[`src/remoteradar/orchestration/flow.py`](src/remoteradar/orchestration/flow.py)
and uses **Prefect 3.x**. The `remoteradar-etl` flow wraps the existing
stages in tasks:

| Task | What it does | Failure behaviour |
| ---- | ------------ | ----------------- |
| `extract_and_load` (×3, one per source) | Reuses `remoteradar.pipeline.run_source` to extract one source and store its payload in `raw.*` | Prefect-native retries: 3 attempts total, exponential backoff (~10s, ~20s) with jitter. A source that exhausts its retries is recorded and the flow moves on. |
| `run_dbt_models` | Shells out to the `dbt` executable (the venv's one, or PATH): `dbt run --project-dir transform --profiles-dir transform` | Non-zero exit fails the task with dbt's output in the logs. No retries — dbt failures are deterministic. |
| `run_quality_checks` | Calls `remoteradar.validate.run_checks` and logs the full report | Any failed or erroring check fails the task (same gate as `remoteradar-validate`). |

Design notes (also documented in the module docstring):

- **One task per source, no duplicated loop.** `pipeline.py` exposes
  `run_source` (the single-source unit of work) which both `run_pipeline`
  (CLI) and the flow share; the flow does not call `run_pipeline`, because
  its loop and error swallowing would duplicate what Prefect tasks do
  natively — and each source gets its own task run, retries and logs in the
  Prefect UI.
- **Partial failure: dbt and validation still run.** If at least one source
  succeeded, `run_dbt_models` and `run_quality_checks` run anyway — partial
  data is worth more than no data, and the staging models dedupe by latest
  ingestion, so the marts stay consistent with the freshest data available.
  Only if **all** sources fail does the flow raise `PipelineError` and skip
  dbt/validation entirely (nothing new to transform).
- **No secrets in Prefect parameters.** Flow/task parameters are stored by
  Prefect Cloud, so the connection string never travels through them: every
  task reads `DATABASE_URL` / `DBT_PG_*` from the environment. The flow
  loads `.env` at start — which also means the `DBT_PG_*` values in `.env`
  reach the dbt subprocess, no manual exporting needed (unlike running dbt
  by hand).
- **Deployment via `flow.serve()` instead of `prefect.yaml`.** Serving needs
  no work pool or separate worker: one long-lived process hosts the daily
  schedule (cron `0 6 * * *`, `America/Sao_Paulo`) and executes the runs —
  the right size for a free-tier, single-machine setup. A worker-based
  `prefect.yaml` only pays off with remote infrastructure; Phase 7 (GitHub
  Actions) may revisit that.

### Activating it with Prefect Cloud

> These steps require real credentials: a Prefect Cloud account (free tier)
> and a reachable PostgreSQL with the raw tables created and the dbt seed
> loaded. Without them the flow *starts* but fails in the extraction/load,
> dbt or validation tasks — expected at this point of the project, where no
> real warehouse has been provisioned yet.

```bash
# 1. Authenticate this machine against your Prefect Cloud workspace
#    (paste the API key created in the Cloud UI when prompted).
prefect cloud login

# 2. One-off setup, if not done yet: raw tables + dbt seed
psql "$DATABASE_URL" -f sql/001_create_raw_remotive_jobs.sql   # ...002, 003
cd transform && dbt seed && cd ..

# 3. Run the flow once, manually — it appears as a flow run in the Cloud UI
python -m remoteradar.orchestration.flow    # or: remoteradar-flow

# 4. Serve the scheduled deployment (blocks; keep the process running)
remoteradar-flow --serve
```

`--serve` registers the `remoteradar-daily` deployment in your workspace
with the daily cron schedule and keeps executing scheduled (and UI-triggered)
runs until the process is stopped — from the Cloud UI you can then trigger
runs, pause the schedule, or re-run a subset of sources via the flow's
`sources` parameter. Run it from the repository root: the dbt project
directory and the `.env` file are resolved from the checkout.

## Tests and lint

```bash
pytest
ruff check .
```

`pytest` covers the extractors and pipeline (with mocked HTTP), the
warehouse check registry, the expectation suite logic tests against the
sample fixtures, and the Prefect flow (run against a temporary local Prefect
API with the source/dbt/validation stages faked) — none of it needs a real
database, network access or Prefect Cloud credentials.

## License

[MIT](LICENSE)
