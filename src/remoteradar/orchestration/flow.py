"""Prefect flow orchestrating the full RemoteRadar run.

One daily run = extract+load every source, build the dbt models, then validate
the warehouse with the Great Expectations suites. Run it once immediately with
``python -m remoteradar.orchestration.flow`` (or ``remoteradar-flow``), or
serve the scheduled deployment with ``remoteradar-flow --serve``.

Design decisions
----------------
- **One task per source, reusing :func:`remoteradar.pipeline.run_source`.**
  Instead of calling ``run_pipeline`` (whose loop, retry-free error swallowing
  and summary would duplicate what Prefect does natively), the pipeline module
  exposes the single-source unit of work and this flow wraps it in a task.
  Retries with exponential backoff use Prefect's native mechanism, and each
  source shows up as its own task run in the Prefect UI.
- **Partial-failure tolerance, same philosophy as the CLI pipeline.** Source
  tasks run sequentially; a failed source (after its retries) is recorded and
  the remaining sources still run. dbt and the quality checks **do run when at
  least one source succeeded**: partial data is worth more than no data, and
  the staging models dedupe by latest ingestion, so building marts on top of
  one fresh source plus older data from the others is exactly the behaviour
  the earlier phases chose. Only when **all** sources fail is
  :class:`~remoteradar.pipeline.PipelineError` raised and dbt/validation
  skipped — there is nothing new to transform, and failing loudly beats
  rebuilding marts to hide a fully broken extraction.
- **No connection string flows through task parameters.** Prefect Cloud
  stores flow/task parameters, and ``DATABASE_URL`` embeds a password, so
  every task reads the environment (via ``.env``) itself. The flow only calls
  :func:`~remoteradar.config.database_url` up front to fail fast when the
  variable is missing, discarding the value.
- **Deployment via** ``flow.serve()`` **instead of** ``prefect.yaml``. Serving
  needs no work pool or separate worker process — one long-lived process on
  any machine (the free-tier scenario for this project) both hosts the daily
  cron schedule and executes the runs. A ``prefect.yaml``/worker setup only
  pays off with remote/dynamic infrastructure, which Phase 7 (GitHub Actions)
  may revisit.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from prefect import flow, get_run_logger, task
from prefect.schedules import Cron
from prefect.tasks import exponential_backoff

from remoteradar.config import database_url, load_env
from remoteradar.pipeline import SOURCES, PipelineError, run_source
from remoteradar.validate import format_report, run_checks

# Repository root (this file lives in src/remoteradar/orchestration/). The
# dbt project is not shipped inside the package, so the flow must run from a
# repository checkout — true for both entry points documented above.
REPO_ROOT = Path(__file__).resolve().parents[3]
TRANSFORM_DIR = REPO_ROOT / "transform"

DEPLOYMENT_NAME = "remoteradar-daily"
# Daily at 06:00 São Paulo time: early enough to have fresh data at the start
# of the working day, late enough to avoid midnight-UTC maintenance windows
# common on free-tier hosting.
DAILY_CRON = "0 6 * * *"
DAILY_CRON_TIMEZONE = "America/Sao_Paulo"


@task(
    retries=2,
    retry_delay_seconds=exponential_backoff(backoff_factor=10),
    retry_jitter_factor=0.5,
)
def extract_and_load(source: str) -> dict[str, Any]:
    """Extract one source and load its payload into the raw schema.

    Network-facing, so it retries: up to 3 attempts total, ~10s then ~20s
    apart (exponential backoff plus jitter so the three sources never hammer
    a recovering API in lockstep). Takes the source *name* rather than the
    extractor callable so the task parameter stays JSON-serializable and
    readable in the Prefect UI.
    """
    logger = get_run_logger()
    logger.info("Extracting and loading source %r", source)
    info = run_source(source, SOURCES[source])
    logger.info(
        "Source %r stored (row id %s, %s jobs)", source, info["row-id"], info["job-count"]
    )
    return info


def _dbt_executable() -> str:
    """Path of the dbt executable, preferring the current environment's one.

    dbt-core has no ``python -m dbt`` entry point, so the task shells out to
    the console script — first the one installed next to ``sys.executable``
    (the project venv), then whatever is on PATH.
    """
    scripts_dir = Path(sys.executable).parent
    for name in ("dbt.exe", "dbt"):
        candidate = scripts_dir / name
        if candidate.exists():
            return str(candidate)
    on_path = shutil.which("dbt")
    if on_path:
        return on_path
    raise RuntimeError(
        "dbt executable not found (neither next to the Python interpreter nor on PATH). "
        "Install the dev dependencies: pip install -e '.[dev]'"
    )


@task
def run_dbt_models() -> None:
    """Build all dbt models (``dbt run``) against the warehouse.

    Runs the same project the README documents for manual use. ``dbt seed``
    is *not* part of the flow: the exchange-rates seed is a fixed snapshot
    loaded once (and after edits) as a setup step, not daily. Because the
    flow loads ``.env`` first, the ``DBT_PG_*`` variables reach the dbt
    subprocess through the inherited environment — no manual exporting.
    """
    logger = get_run_logger()
    command = [
        _dbt_executable(),
        "run",
        "--project-dir",
        str(TRANSFORM_DIR),
        "--profiles-dir",
        str(TRANSFORM_DIR),
    ]
    logger.info("Running %s", " ".join(command))
    completed = subprocess.run(command, capture_output=True, text=True, cwd=TRANSFORM_DIR)
    if completed.stdout:
        logger.info("dbt output:\n%s", completed.stdout)
    if completed.returncode != 0:
        if completed.stderr:
            logger.error("dbt stderr:\n%s", completed.stderr)
        raise RuntimeError(
            f"dbt run failed with exit code {completed.returncode}; "
            "see the dbt output logged above for the failing model(s)"
        )


@task
def run_quality_checks() -> None:
    """Run every Great Expectations warehouse check and fail unless all pass.

    Same gate semantics as the ``remoteradar-validate`` CLI: the full report
    is logged either way, and any failed or erroring check fails the task
    (and therefore the flow run) so a broken warehouse is visible in Prefect.
    """
    logger = get_run_logger()
    results = run_checks()
    logger.info("%s", format_report(results))
    not_passed = [result for result in results if result.status != "passed"]
    if not_passed:
        raise RuntimeError(
            f"{len(not_passed)} of {len(results)} warehouse quality check(s) did not pass"
        )


@flow(name="remoteradar-etl")
def remoteradar_etl(sources: list[str] | None = None) -> dict[str, Any]:
    """Full daily run: extract+load each source, dbt run, quality validation.

    Args:
        sources: subset of source names to run (default: all registered
            sources). Useful for re-running a single source from the UI.

    Returns:
        Extraction summary, same shape as ``run_pipeline``:
        ``{"succeeded": {source: {"row-id", "job-count"}},
        "failed": {source: error message}}``.

    Raises:
        PipelineError: if every source failed (dbt and validation are skipped).
        ConfigError: if DATABASE_URL is not configured.
    """
    logger = get_run_logger()
    load_env()
    database_url()  # fail fast before any API call; value stays out of Prefect

    names = list(sources) if sources is not None else list(SOURCES)
    unknown = [name for name in names if name not in SOURCES]
    if unknown:
        raise ValueError(f"Unknown source(s) {unknown}; expected a subset of {list(SOURCES)}")

    succeeded: dict[str, dict[str, Any]] = {}
    failed: dict[str, str] = {}
    for name in names:
        # return_state (instead of a plain call) keeps the partial-failure
        # philosophy: a source that failed all its retries is recorded and
        # the remaining sources still run.
        state = extract_and_load(name, return_state=True)
        if state.is_completed():
            succeeded[name] = state.result()
        else:
            error = state.result(raise_on_failure=False)
            failed[name] = str(error)
            logger.warning("Source %r failed after retries: %s", name, error)

    if names and not succeeded:
        raise PipelineError(
            "All sources failed during the flow run: "
            + "; ".join(f"{name}: {message}" for name, message in failed.items())
        )
    if failed:
        logger.warning(
            "Continuing with partial data: %s failed, %s succeeded. dbt and the quality "
            "checks still run — staging dedupes by latest ingestion, so the marts stay "
            "consistent with the freshest data available.",
            sorted(failed),
            sorted(succeeded),
        )

    run_dbt_models()
    run_quality_checks()
    return {"succeeded": succeeded, "failed": failed}


def serve_deployment() -> None:
    """Serve the daily-scheduled deployment from this process (blocks).

    Registers the deployment on the configured Prefect API (Prefect Cloud
    after ``prefect cloud login``) and keeps executing its scheduled and
    manually triggered runs until the process is stopped.
    """
    remoteradar_etl.serve(
        name=DEPLOYMENT_NAME,
        schedules=[Cron(DAILY_CRON, timezone=DAILY_CRON_TIMEZONE)],
        tags=["remoteradar"],
    )


def main() -> None:
    """Entry point: run the flow once, or serve the deployment with --serve."""
    import argparse

    parser = argparse.ArgumentParser(description="RemoteRadar Prefect orchestration")
    parser.add_argument(
        "--serve",
        action="store_true",
        help=f"serve the {DEPLOYMENT_NAME!r} deployment (daily cron) instead of running once",
    )
    arguments = parser.parse_args()
    if arguments.serve:
        serve_deployment()
    else:
        remoteradar_etl()


if __name__ == "__main__":
    main()
