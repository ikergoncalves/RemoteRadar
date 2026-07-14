# RemoteRadar

Python ETL pipeline that collects remote tech job postings from public APIs,
cleans and transforms the data, loads it into a PostgreSQL data warehouse and —
in upcoming phases — powers a trends dashboard: most requested languages,
salary ranges, companies hiring remote the most, and evolution over time.

> **Phased project.** This is Phase 1 of 7: repository structure and extraction
> of the first source (Remotive), storing the raw payload in the `raw` schema
> of PostgreSQL. New sources (RemoteOK, Adzuna), transformations and the
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
│   └── extract/
│       └── remotive.py      # Remotive API extraction
├── sql/
│   └── 001_create_raw_remotive_jobs.sql   # Landing table DDL
├── tests/                   # Tests with mocked HTTP (never hits the real API)
├── .env.example             # Documented environment variables
└── pyproject.toml           # Dependencies and tool configuration
```

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
# Copy .env.example to .env and fill in DATABASE_URL
```

### Environment variables

| Variable           | Required   | Description                                                              |
| ------------------ | ---------- | ------------------------------------------------------------------------ |
| `DATABASE_URL`     | Yes (load) | PostgreSQL connection string (`postgresql://user:pass@host:5432/db`)     |
| `REMOTIVE_API_URL` | No         | Remotive API base URL (default: public endpoint)                         |

### Create the raw table in PostgreSQL

```bash
psql "$DATABASE_URL" -f sql/001_create_raw_remotive_jobs.sql
```

### Run the extraction

```bash
python -m remoteradar.extract.remotive
```

Fetches job postings from Remotive's tech categories (Software Development,
Artificial Intelligence, Data and Analytics, Devops, Quality Assurance and
Information Technology — one call per category), consolidates everything into
a single payload and inserts the result (JSONB) into `raw.remotive_jobs`, with
an ingestion timestamp. If one category fails, the error is logged and the
extraction continues with the remaining ones, recording the failures in the
`failed-categories` field of the stored payload; it only aborts if all of them
fail. Without `DATABASE_URL` configured, the script fails with an error
message explaining how to fix it.

### Tests and lint

```bash
pytest
ruff check .
```

## License

[MIT](LICENSE)
