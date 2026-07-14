"""Tests for the pipeline orchestration (extractors and loader injected as fakes)."""

from __future__ import annotations

from typing import Any

import pytest

from remoteradar.load import RAW_TABLES
from remoteradar.pipeline import SOURCES, PipelineError, run_pipeline

PAYLOAD_A = {"job-count": 2, "jobs": [{"id": 1}, {"id": 2}]}
PAYLOAD_B = {"job-count": 1, "jobs": [{"id": 3}]}


class FakeLoader:
    """Records every load call and returns sequential row ids."""

    def __init__(self, failing: set[str] | None = None) -> None:
        self.calls: list[tuple[str, dict[str, Any], str | None]] = []
        self.failing = failing or set()

    def __call__(self, source: str, payload: dict[str, Any], *, dsn: str | None = None) -> int:
        if source in self.failing:
            raise ConnectionError(f"could not reach the database for {source}")
        self.calls.append((source, payload, dsn))
        return len(self.calls)


def failing_extractor() -> dict[str, Any]:
    raise RuntimeError("source API is down")


def test_run_pipeline_loads_every_source() -> None:
    loader = FakeLoader()
    sources = {"remotive": lambda: PAYLOAD_A, "remoteok": lambda: PAYLOAD_B}

    summary = run_pipeline(sources, dsn="postgresql://test", loader=loader)

    assert loader.calls == [
        ("remotive", PAYLOAD_A, "postgresql://test"),
        ("remoteok", PAYLOAD_B, "postgresql://test"),
    ]
    assert summary["succeeded"] == {
        "remotive": {"row-id": 1, "job-count": 2},
        "remoteok": {"row-id": 2, "job-count": 1},
    }
    assert summary["failed"] == {}


def test_run_pipeline_continues_when_one_extraction_fails() -> None:
    loader = FakeLoader()
    sources = {
        "remotive": failing_extractor,
        "remoteok": lambda: PAYLOAD_B,
    }

    summary = run_pipeline(sources, loader=loader)

    assert [call[0] for call in loader.calls] == ["remoteok"]
    assert list(summary["succeeded"]) == ["remoteok"]
    assert summary["failed"] == {"remotive": "source API is down"}


def test_run_pipeline_continues_when_one_load_fails() -> None:
    loader = FakeLoader(failing={"remotive"})
    sources = {
        "remotive": lambda: PAYLOAD_A,
        "remoteok": lambda: PAYLOAD_B,
    }

    summary = run_pipeline(sources, loader=loader)

    assert [call[0] for call in loader.calls] == ["remoteok"]
    assert list(summary["succeeded"]) == ["remoteok"]
    assert "could not reach the database" in summary["failed"]["remotive"]


def test_run_pipeline_all_sources_failing_raises_pipeline_error() -> None:
    loader = FakeLoader()
    sources = {
        "remotive": failing_extractor,
        "remoteok": failing_extractor,
    }

    with pytest.raises(PipelineError, match="All sources failed") as excinfo:
        run_pipeline(sources, loader=loader)

    assert "remotive" in str(excinfo.value)
    assert "remoteok" in str(excinfo.value)
    assert loader.calls == []


def test_default_sources_match_the_raw_tables() -> None:
    # Every registered source must have a landing table, or the load would
    # reject it at runtime.
    assert set(SOURCES) == {"remotive", "remoteok", "adzuna"}
    assert set(SOURCES) <= set(RAW_TABLES)
