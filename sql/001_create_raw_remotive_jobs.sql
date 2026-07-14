-- Tabela de landing para as respostas brutas da API da Remotive.
-- Aplicar manualmente por enquanto (psql -f sql/001_create_raw_remotive_jobs.sql);
-- migrations gerenciadas chegam em fase posterior, junto com o dbt.

CREATE SCHEMA IF NOT EXISTS raw;

CREATE TABLE IF NOT EXISTS raw.remotive_jobs (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    source      TEXT        NOT NULL DEFAULT 'remotive',
    payload     JSONB       NOT NULL
);

COMMENT ON TABLE raw.remotive_jobs IS
    'Respostas brutas (sem transformação) da API da Remotive; uma linha por execução da extração.';
COMMENT ON COLUMN raw.remotive_jobs.ingested_at IS 'Timestamp da coleta (UTC).';
COMMENT ON COLUMN raw.remotive_jobs.payload IS 'Payload JSON completo retornado pela API.';
