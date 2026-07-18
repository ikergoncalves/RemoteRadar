"""Warehouse validation: run the expectation suites against the real database.

Given a configured DATABASE_URL, runs every check in
:data:`remoteradar.quality.WAREHOUSE_CHECKS` against the warehouse and prints
a per-check pass/fail report. The Prefect flow
(:mod:`remoteradar.orchestration.flow`) calls :func:`run_checks` as its
quality gate after the pipeline + dbt run; CI (GitHub Actions, Phase 7) will
too. It also runs standalone::

    python -m remoteradar.validate   # or simply: remoteradar-validate

Checks are independent: one broken (or missing) table is reported and the
run continues with the rest — the same partial-failure philosophy as the
pipeline. The exit code is 0 only if every check passed.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from remoteradar.config import analytics_schema, database_url
from remoteradar.quality.checks import WAREHOUSE_CHECKS, WarehouseCheck


@dataclass
class CheckResult:
    """Outcome of one warehouse check."""

    check: str
    suite: str
    status: str  # "passed" | "failed" | "error"
    passed_expectations: int = 0
    total_expectations: int = 0
    failures: list[str] = field(default_factory=list)
    error: str | None = None


def _to_sqlalchemy_url(dsn: str) -> str:
    """Rewrite a plain postgres DSN to use the psycopg (v3) SQLAlchemy driver.

    Bare ``postgresql://`` URLs make SQLAlchemy default to psycopg2, which
    this project does not install (it already depends on psycopg 3 for the
    load step). DSNs that name a driver explicitly pass through unchanged.
    """
    for prefix in ("postgresql://", "postgres://"):
        if dsn.startswith(prefix):
            return "postgresql+psycopg://" + dsn.removeprefix(prefix)
    return dsn


def _exception_message(result) -> str | None:
    """Exception message of an erroring expectation result, if it raised one.

    GX 1.x uses two shapes for ``exception_info``: a flat dict with a
    ``raised_exception`` flag when evaluation went through, or a dict keyed by
    metric id (each value carrying ``exception_message``) when a metric raised
    (e.g. the column does not exist).
    """
    info = result.exception_info or {}
    if "raised_exception" in info:
        return info.get("exception_message") if info["raised_exception"] else None
    messages = [
        value.get("exception_message") for value in info.values() if isinstance(value, dict)
    ]
    return next((message for message in messages if message), "unknown evaluation error")


def _describe_failure(result) -> str:
    """One human-readable line for a failed expectation result."""
    config = result.expectation_config
    kwargs = config.kwargs
    column = kwargs.get("column") or f"{kwargs.get('column_A')}/{kwargs.get('column_B')}"
    exception_message = _exception_message(result)
    if exception_message is not None:
        detail = f"raised: {exception_message}"
    else:
        unexpected = result.result.get("unexpected_count")
        detail = f"{unexpected} unexpected value(s)" if unexpected is not None else "failed"
    return f"{config.type} on {column}: {detail}"


def run_checks(
    dsn: str | None = None,
    checks: tuple[WarehouseCheck, ...] = WAREHOUSE_CHECKS,
) -> list[CheckResult]:
    """Run every warehouse check and return one :class:`CheckResult` each.

    Args:
        dsn: PostgreSQL connection string; defaults to the DATABASE_URL
            environment variable.
        checks: checks to run (default: the full registry).

    Raises:
        ConfigError: if no ``dsn`` is given and DATABASE_URL is not set.
    """
    # Telemetry off before GX is imported: validation may run in CI.
    os.environ.setdefault("GX_ANALYTICS_ENABLED", "false")
    import great_expectations as gx

    connection_string = _to_sqlalchemy_url(dsn or database_url())
    dbt_schema = analytics_schema()
    context = gx.get_context(mode="ephemeral")
    try:
        datasource = context.data_sources.add_postgres(
            name="warehouse", connection_string=connection_string
        )
    except Exception as exc:
        raise ConnectionError(f"Could not connect to the warehouse: {exc}") from exc

    results: list[CheckResult] = []
    for check in checks:
        suite = check.suite()
        # Broad catch by design: a missing table (marts not built yet) or a
        # bad projection must not stop the remaining checks from running.
        try:
            if check.query is not None:
                asset = datasource.add_query_asset(name=check.name, query=check.query)
            else:
                asset = datasource.add_table_asset(
                    name=check.name, table_name=check.table_name, schema_name=dbt_schema
                )
            batch = asset.add_batch_definition_whole_table(f"{check.name}_batch").get_batch()
            validation = batch.validate(suite)
        except Exception as exc:
            results.append(
                CheckResult(check=check.name, suite=suite.name, status="error", error=str(exc))
            )
            continue

        failures = [_describe_failure(r) for r in validation.results if not r.success]
        results.append(
            CheckResult(
                check=check.name,
                suite=suite.name,
                status="passed" if validation.success else "failed",
                passed_expectations=len(validation.results) - len(failures),
                total_expectations=len(validation.results),
                failures=failures,
            )
        )
    return results


def format_report(results: list[CheckResult]) -> str:
    """Render the results as the plain-text report printed by :func:`main`."""
    lines = ["== RemoteRadar data quality report =="]
    for result in results:
        if result.status == "error":
            lines.append(f"ERROR   {result.check} (suite {result.suite})")
            lines.append(f"        {result.error}")
            continue
        status = "PASSED " if result.status == "passed" else "FAILED "
        lines.append(
            f"{status} {result.check} (suite {result.suite}, "
            f"{result.passed_expectations}/{result.total_expectations} expectations)"
        )
        lines.extend(f"        - {failure}" for failure in result.failures)
    passed = sum(1 for r in results if r.status == "passed")
    failed = sum(1 for r in results if r.status == "failed")
    errors = sum(1 for r in results if r.status == "error")
    lines.append(f"Summary: {passed} passed, {failed} failed, {errors} error(s)")
    return "\n".join(lines)


def main() -> None:
    """Validate the warehouse against every expectation suite and report."""
    import sys

    from remoteradar.config import ConfigError, load_env

    load_env()
    try:
        results = run_checks()
    except (ConfigError, ConnectionError) as exc:
        sys.exit(f"Error: {exc}")
    print(format_report(results))
    if any(result.status != "passed" for result in results):
        sys.exit(1)


if __name__ == "__main__":
    main()
