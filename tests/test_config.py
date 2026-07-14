"""Tests for environment-based configuration."""

from __future__ import annotations

import pytest

from remoteradar.config import DEFAULT_REMOTIVE_API_URL, ConfigError, database_url, remotive_api_url


def test_database_url_missing_raises_clear_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)

    with pytest.raises(ConfigError, match="DATABASE_URL"):
        database_url()


def test_database_url_reads_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@localhost:5432/remoteradar")

    assert database_url() == "postgresql://u:p@localhost:5432/remoteradar"


def test_remotive_api_url_defaults_to_public_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("REMOTIVE_API_URL", raising=False)

    assert remotive_api_url() == DEFAULT_REMOTIVE_API_URL


def test_remotive_api_url_reads_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REMOTIVE_API_URL", "https://api.test/remote-jobs")

    assert remotive_api_url() == "https://api.test/remote-jobs"
