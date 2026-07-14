-- Landing table for raw Adzuna API responses.
-- Apply manually for now (psql -f sql/003_create_raw_adzuna_jobs.sql);
-- managed migrations arrive in a later phase, together with dbt.

CREATE SCHEMA IF NOT EXISTS raw;

CREATE TABLE IF NOT EXISTS raw.adzuna_jobs (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    source      TEXT        NOT NULL DEFAULT 'adzuna',
    payload     JSONB       NOT NULL
);

COMMENT ON TABLE raw.adzuna_jobs IS
    'Raw (untransformed) responses from the Adzuna API; one row per extraction run.';
COMMENT ON COLUMN raw.adzuna_jobs.ingested_at IS 'Ingestion timestamp (UTC).';
COMMENT ON COLUMN raw.adzuna_jobs.payload IS 'Consolidated JSON payload built from the API responses.';
