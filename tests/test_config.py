"""Tests for environment-based configuration."""

from __future__ import annotations

import pytest

from remoteradar.config import (
    DEFAULT_ADZUNA_COUNTRY,
    DEFAULT_REMOTEOK_API_URL,
    DEFAULT_REMOTIVE_API_URL,
    ConfigError,
    adzuna_country,
    adzuna_credentials,
    database_url,
    remoteok_api_url,
    remotive_api_url,
)


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


def test_remoteok_api_url_defaults_to_public_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("REMOTEOK_API_URL", raising=False)

    assert remoteok_api_url() == DEFAULT_REMOTEOK_API_URL


def test_remoteok_api_url_reads_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REMOTEOK_API_URL", "https://api.test/remoteok")

    assert remoteok_api_url() == "https://api.test/remoteok"


def test_adzuna_country_defaults_to_gb(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ADZUNA_COUNTRY", raising=False)

    assert adzuna_country() == DEFAULT_ADZUNA_COUNTRY == "gb"


def test_adzuna_country_reads_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ADZUNA_COUNTRY", "us")

    assert adzuna_country() == "us"


@pytest.mark.parametrize("missing", ["ADZUNA_APP_ID", "ADZUNA_APP_KEY", "both"])
def test_adzuna_credentials_missing_raises_clear_error(
    monkeypatch: pytest.MonkeyPatch, missing: str
) -> None:
    monkeypatch.setenv("ADZUNA_APP_ID", "test-app-id")
    monkeypatch.setenv("ADZUNA_APP_KEY", "test-app-key")
    for var in ("ADZUNA_APP_ID", "ADZUNA_APP_KEY"):
        if missing in (var, "both"):
            monkeypatch.delenv(var)

    with pytest.raises(ConfigError, match="ADZUNA_APP_ID and/or ADZUNA_APP_KEY"):
        adzuna_credentials()


def test_adzuna_credentials_read_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ADZUNA_APP_ID", "test-app-id")
    monkeypatch.setenv("ADZUNA_APP_KEY", "test-app-key")

    assert adzuna_credentials() == ("test-app-id", "test-app-key")
