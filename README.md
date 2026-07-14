# RemoteRadar

Python ETL pipeline that collects remote tech job postings from public APIs,
cleans and transforms the data, loads it into a PostgreSQL data warehouse and —
in upcoming phases — powers a trends dashboard: most requested languages,
salary ranges, companies hiring remote the most, and evolution over time.

> **Phased project.** This is Phase 2 of 7: three extraction sources
> (Remotive, RemoteOK, Adzuna) landing raw JSONB payloads in PostgreSQL, a
> pipeline CLI that orchestrates them with partial-failure tolerance, and a
> dbt project with staging models that parse the raw payloads into one row
> per job. Orchestration with Prefect, data quality checks and the dashboard
> arrive in the next phases.

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
├── transform/               # dbt project (sources + staging models)
│   ├── dbt_project.yml
│   ├── profiles.yml         # Connection via DBT_PG_* env vars (no secrets)
│   └── models/staging/      # stg_remotive_jobs, stg_remoteok_jobs, stg_adzuna_jobs
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

The dbt project lives in [`transform/`](transform/). It defines the three raw
tables as sources and a staging layer that parses each JSONB payload into one
row per job with normalized columns (`job_id`, `title`, `company`,
`category`/`tags`, `location`, `salary_min`/`salary_max`/`salary_text`,
`published_at`, `url`, `source`), deduplicated to the latest ingestion of
each job. Column meanings and origins are documented in
[`transform/models/staging/schema.yml`](transform/models/staging/schema.yml).

`transform/profiles.yml` is committed (it contains no secrets): the
connection comes from `DBT_PG_HOST`, `DBT_PG_PORT`, `DBT_PG_USER`,
`DBT_PG_PASSWORD`, `DBT_PG_DBNAME` and `DBT_PG_SCHEMA` — the same values as
`DATABASE_URL`, split into components, since dbt-postgres does not accept a
connection string. **dbt does not read `.env` files**: export these variables
in your shell before running dbt.

```bash
cd transform
dbt debug     # checks connection and project setup
dbt run       # builds the staging views
dbt test      # runs the not_null/unique tests from schema.yml
```

`dbt run`/`dbt test` require a reachable PostgreSQL with the raw tables
created; `dbt parse` validates the project without a database.

### Tests and lint

```bash
pytest
ruff check .
```

## License

[MIT](LICENSE)
