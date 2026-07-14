-- Landing table for raw RemoteOK API responses.
-- Apply manually for now (psql -f sql/002_create_raw_remoteok_jobs.sql);
-- managed migrations arrive in a later phase, together with dbt.

CREATE SCHEMA IF NOT EXISTS raw;

CREATE TABLE IF NOT EXISTS raw.remoteok_jobs (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    source      TEXT        NOT NULL DEFAULT 'remoteok',
    payload     JSONB       NOT NULL
);

COMMENT ON TABLE raw.remoteok_jobs IS
    'Raw (untransformed) responses from the RemoteOK API; one row per extraction run.';
COMMENT ON COLUMN raw.remoteok_jobs.ingested_at IS 'Ingestion timestamp (UTC).';
COMMENT ON COLUMN raw.remoteok_jobs.payload IS 'Consolidated JSON payload built from the API responses.';
