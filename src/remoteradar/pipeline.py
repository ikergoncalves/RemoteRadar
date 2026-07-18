"""Orchestration of all source extractions into the raw warehouse schema.

Runs standalone via ``python -m remoteradar.pipeline`` (useful for debugging a
run without Prefect). The Prefect flow (:mod:`remoteradar.orchestration.flow`)
does not call :func:`run_pipeline` directly: it wraps :func:`run_source` — the
single-source unit of work factored out below — in one task per source, so
retries and partial-failure handling live in the orchestrator while the
extract-and-load logic stays here, shared by both entry points.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from typing import Any

from remoteradar.extract import adzuna, remoteok, remotive
from remoteradar.load import insert_raw_payload

logger = logging.getLogger(__name__)

# Source name (must match a remoteradar.load.RAW_TABLES key) -> zero-arg
# extractor returning that source's consolidated payload.
SOURCES: dict[str, Callable[[], dict[str, Any]]] = {
    "remotive": remotive.fetch_tech_jobs,
    "remoteok": remoteok.fetch_tech_jobs,
    "adzuna": adzuna.fetch_tech_jobs,
}


class PipelineError(Exception):
    """Raised when every source in the pipeline fails."""


def run_source(
    name: str,
    extract: Callable[[], dict[str, Any]],
    *,
    dsn: str | None = None,
    loader: Callable[..., int] = insert_raw_payload,
) -> dict[str, Any]:
    """Extract one source and load its payload into the raw schema.

    This is the single-source unit of work shared by :func:`run_pipeline`
    (which loops over all sources tolerating failures) and the Prefect flow
    (which wraps it in one task per source). Unlike :func:`run_pipeline`, it
    lets any extraction or load error propagate — the caller decides how to
    handle it (record and continue, or let Prefect retry).

    Args:
        name: source name; must be known to :func:`remoteradar.load.insert_raw_payload`.
        extract: zero-arg extractor returning the source's consolidated payload.
        dsn: PostgreSQL connection string; defaults to the DATABASE_URL
            environment variable.
        loader: callable ``(source, payload, *, dsn) -> row id`` storing one
            payload (injectable for tests).

    Returns:
        ``{"row-id": <raw table row id>, "job-count": <declared job count>}``.
    """
    payload = extract()
    row_id = loader(name, payload, dsn=dsn)
    return {"row-id": row_id, "job-count": payload.get("job-count")}


def run_pipeline(
    sources: Mapping[str, Callable[[], dict[str, Any]]] | None = None,
    *,
    dsn: str | None = None,
    loader: Callable[..., int] = insert_raw_payload,
) -> dict[str, Any]:
    """Extract and load every source, tolerating individual source failures.

    Applies the same partial-failure principle used inside each extractor:
    a source that fails (extraction or load) is logged and recorded, and the
    pipeline continues with the remaining ones — partial data is worth more
    than no data. Only if every source fails is :class:`PipelineError`
    raised, since the run produced nothing.

    Args:
        sources: mapping of source name -> extractor (default: :data:`SOURCES`).
            Names must be known to :func:`remoteradar.load.insert_raw_payload`.
        dsn: PostgreSQL connection string; defaults to the DATABASE_URL
            environment variable.
        loader: callable ``(source, payload, *, dsn) -> row id`` storing one
            payload (default: :func:`remoteradar.load.insert_raw_payload`;
            injectable for tests).

    Returns:
        Summary: ``{"succeeded": {source: {"row-id", "job-count"}},
        "failed": {source: error message}}``.

    Raises:
        PipelineError: if all sources fail.
    """
    if sources is None:
        sources = SOURCES
    succeeded: dict[str, dict[str, Any]] = {}
    failed: dict[str, str] = {}

    for name, extract in sources.items():
        # Broad catch by design: one misbehaving source (API change, bad
        # credentials, DB hiccup mid-run) must never take down the others.
        try:
            info = run_source(name, extract, dsn=dsn, loader=loader)
        except Exception as exc:
            logger.exception("Source %r failed, continuing with the rest", name)
            failed[name] = str(exc)
            continue
        succeeded[name] = info
        logger.info(
            "Source %r stored (row id %s, %s jobs)", name, info["row-id"], info["job-count"]
        )

    if sources and not succeeded:
        raise PipelineError(
            "All sources failed during the pipeline run: "
            + "; ".join(f"{name}: {msg}" for name, msg in failed.items())
        )
    return {"succeeded": succeeded, "failed": failed}


def main() -> None:
    """Run the full extract-and-load pipeline for all sources."""
    import sys

    from remoteradar.config import ConfigError, database_url, load_env

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    load_env()
    try:
        dsn = database_url()  # fail fast: validate config before calling any API
        summary = run_pipeline(dsn=dsn)
    except (ConfigError, PipelineError) as exc:
        sys.exit(f"Error: {exc}")
    for name, info in summary["succeeded"].items():
        print(f"{name}: stored row {info['row-id']} ({info['job-count']} jobs)")
    for name, message in summary["failed"].items():
        print(f"{name}: FAILED - {message}")


if __name__ == "__main__":
    main()
