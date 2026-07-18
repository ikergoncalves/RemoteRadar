"""Tests for the Prefect orchestration flow (fakes only — no Cloud, no database).

The flow runs against ``prefect_test_harness`` (a temporary local Prefect API
backed by SQLite), with the three stage boundaries replaced by fakes: the
single-source runner (``run_source``), the dbt subprocess and the quality
checks. Same philosophy as ``test_pipeline.py``, one level up — orchestration
logic is exercised for real (ordering, retries, partial failure), while
network, database and Prefect Cloud stay out of the picture.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from prefect.testing.utilities import prefect_test_harness

from remoteradar.orchestration import flow as flow_module
from remoteradar.pipeline import PipelineError
from remoteradar.validate import CheckResult

# Captured before any fixture patches the module attribute, so the retry
# configuration test sees the task exactly as production code defines it.
EXTRACT_TASK = flow_module.extract_and_load


@pytest.fixture(scope="module", autouse=True)
def prefect_backend():
    """Run every flow in this module against a temporary local Prefect API."""
    with prefect_test_harness():
        yield


@pytest.fixture(autouse=True)
def fast_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the native retry count but drop the delays so failure tests are fast."""
    monkeypatch.setattr(
        flow_module, "extract_and_load", EXTRACT_TASK.with_options(retry_delay_seconds=0)
    )


@pytest.fixture(autouse=True)
def fake_database_url(monkeypatch: pytest.MonkeyPatch) -> None:
    """Satisfy the flow's fail-fast config check without a real warehouse."""
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:secret@localhost:5432/remoteradar")


@pytest.fixture()
def events() -> list[str]:
    """Chronological record of the stages the flow actually executed."""
    return []


@pytest.fixture()
def stages(events: list[str], monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """Patch the three stage boundaries; the returned controller makes them fail."""
    controller = SimpleNamespace(
        failing_sources=set(),
        attempts={},
        dbt_returncode=0,
        check_results=[CheckResult(check="all_good", suite="raw_jobs", status="passed")],
    )

    def fake_run_source(name: str, extract: Any, **kwargs: Any) -> dict[str, Any]:
        controller.attempts[name] = controller.attempts.get(name, 0) + 1
        if name in controller.failing_sources:
            raise RuntimeError(f"{name} API is down")
        events.append(f"source:{name}")
        return {"row-id": len(events), "job-count": 5}

    def fake_subprocess_run(command: list[str], **kwargs: Any) -> SimpleNamespace:
        events.append("dbt")
        return SimpleNamespace(
            returncode=controller.dbt_returncode, stdout="dbt output", stderr="dbt error"
        )

    def fake_run_checks() -> list[CheckResult]:
        events.append("checks")
        return controller.check_results

    monkeypatch.setattr(flow_module, "run_source", fake_run_source)
    monkeypatch.setattr(flow_module, "_dbt_executable", lambda: "dbt")
    monkeypatch.setattr(flow_module.subprocess, "run", fake_subprocess_run)
    monkeypatch.setattr(flow_module, "run_checks", fake_run_checks)
    return controller


def test_flow_runs_sources_then_dbt_then_checks(
    events: list[str], stages: SimpleNamespace
) -> None:
    summary = flow_module.remoteradar_etl()

    assert events == ["source:remotive", "source:remoteok", "source:adzuna", "dbt", "checks"]
    assert set(summary["succeeded"]) == {"remotive", "remoteok", "adzuna"}
    assert summary["failed"] == {}


def test_flow_continues_with_partial_data_when_one_source_fails(
    events: list[str], stages: SimpleNamespace
) -> None:
    stages.failing_sources = {"remotive"}

    summary = flow_module.remoteradar_etl()

    # dbt and the quality checks still run on the partial data.
    assert events == ["source:remoteok", "source:adzuna", "dbt", "checks"]
    assert set(summary["succeeded"]) == {"remoteok", "adzuna"}
    assert "remotive API is down" in summary["failed"]["remotive"]


def test_failing_source_is_retried_via_the_native_mechanism(
    events: list[str], stages: SimpleNamespace
) -> None:
    stages.failing_sources = {"adzuna"}

    flow_module.remoteradar_etl()

    # 1 initial attempt + the task's configured retries, healthy sources once.
    assert stages.attempts["adzuna"] == 1 + EXTRACT_TASK.retries
    assert stages.attempts["remotive"] == 1


def test_flow_raises_and_skips_dbt_when_all_sources_fail(
    events: list[str], stages: SimpleNamespace
) -> None:
    stages.failing_sources = {"remotive", "remoteok", "adzuna"}

    with pytest.raises(PipelineError, match="All sources failed") as excinfo:
        flow_module.remoteradar_etl()

    assert "remotive" in str(excinfo.value)
    assert events == []  # neither dbt nor the checks ran


def test_flow_fails_without_running_checks_when_dbt_fails(
    events: list[str], stages: SimpleNamespace
) -> None:
    stages.dbt_returncode = 2

    with pytest.raises(RuntimeError, match="dbt run failed with exit code 2"):
        flow_module.remoteradar_etl()

    assert "checks" not in events


def test_flow_fails_when_a_quality_check_does_not_pass(
    events: list[str], stages: SimpleNamespace
) -> None:
    stages.check_results = [
        CheckResult(check="raw_remotive_jobs", suite="raw_jobs", status="passed"),
        CheckResult(check="mart_companies", suite="mart_companies", status="failed"),
    ]

    with pytest.raises(RuntimeError, match="1 of 2 warehouse quality check"):
        flow_module.remoteradar_etl()

    assert events[-1] == "checks"  # the gate ran and the report was produced


def test_flow_accepts_a_subset_of_sources(events: list[str], stages: SimpleNamespace) -> None:
    summary = flow_module.remoteradar_etl(sources=["remoteok"])

    assert events == ["source:remoteok", "dbt", "checks"]
    assert set(summary["succeeded"]) == {"remoteok"}


def test_flow_rejects_unknown_sources(stages: SimpleNamespace) -> None:
    with pytest.raises(ValueError, match="Unknown source"):
        flow_module.remoteradar_etl(sources=["remotive", "linkedin"])


def test_extract_task_uses_native_retries_with_backoff() -> None:
    # Guards the requirement that retry/backoff is Prefect-native, not manual.
    assert EXTRACT_TASK.retries == 2
    assert EXTRACT_TASK.retry_jitter_factor == 0.5
    assert callable(EXTRACT_TASK.retry_delay_seconds) or EXTRACT_TASK.retry_delay_seconds
