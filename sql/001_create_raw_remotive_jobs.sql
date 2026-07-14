-- Landing table for raw Remotive API responses.
-- Apply manually for now (psql -f sql/001_create_raw_remotive_jobs.sql);
-- managed migrations arrive in a later phase, together with dbt.

CREATE SCHEMA IF NOT EXISTS raw;

CREATE TABLE IF NOT EXISTS raw.remotive_jobs (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    source      TEXT        NOT NULL DEFAULT 'remotive',
    payload     JSONB       NOT NULL
);

COMMENT ON TABLE raw.remotive_jobs IS
    'Raw (untransformed) responses from the Remotive API; one row per extraction run.';
COMMENT ON COLUMN raw.remotive_jobs.ingested_at IS 'Ingestion timestamp (UTC).';
COMMENT ON COLUMN raw.remotive_jobs.payload IS 'Full JSON payload returned by the API.';
