"""Tests for the Adzuna extraction (HTTP mocked via httpx.MockTransport)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx
import pytest

from remoteradar.config import ConfigError
from remoteradar.extract.adzuna import AdzunaError, fetch_jobs, fetch_tech_jobs

API_URL = "https://api.test/v1/api/jobs"

PAGE_1 = {
    "count": 3,
    "results": [
        {"id": 1, "title": "Python Developer", "company": {"display_name": "Acme"}},
        {"id": 2, "title": "Data Engineer", "company": {"display_name": "Globex"}},
    ],
}
PAGE_2 = {
    "count": 3,
    "results": [
        {"id": 3, "title": "SRE", "company": {"display_name": "Initech"}},
    ],
}


@pytest.fixture
def credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ADZUNA_APP_ID", "test-app-id")
    monkeypatch.setenv("ADZUNA_APP_KEY", "test-app-key")


def make_client(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_missing_credentials_fail_before_any_http_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ADZUNA_APP_ID", raising=False)
    monkeypatch.delenv("ADZUNA_APP_KEY", raising=False)
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json=PAGE_1)

    with make_client(handler) as client:
        with pytest.raises(ConfigError, match="ADZUNA_APP_ID"):
            fetch_jobs(client=client, api_url=API_URL)
        with pytest.raises(ConfigError, match="ADZUNA_APP_ID"):
            fetch_tech_jobs(client=client, api_url=API_URL)

    assert calls == []


@pytest.mark.usefixtures("credentials")
def test_fetch_jobs_success_returns_raw_payload() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=PAGE_1)

    with make_client(handler) as client:
        payload = fetch_jobs(client=client, api_url=API_URL)

    assert payload == PAGE_1


@pytest.mark.usefixtures("credentials")
def test_fetch_jobs_builds_url_and_sends_auth_params() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["params"] = dict(request.url.params)
        return httpx.Response(200, json=PAGE_1)

    with make_client(handler) as client:
        fetch_jobs(2, country="us", what="python", client=client, api_url=API_URL)

    assert captured["path"] == "/v1/api/jobs/us/search/2"
    assert captured["params"] == {
        "app_id": "test-app-id",
        "app_key": "test-app-key",
        "results_per_page": "50",
        "category": "it-jobs",
        "what": "python",
    }


@pytest.mark.usefixtures("credentials")
def test_fetch_jobs_defaults_to_gb_country(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ADZUNA_COUNTRY", raising=False)
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        return httpx.Response(200, json=PAGE_1)

    with make_client(handler) as client:
        fetch_jobs(client=client, api_url=API_URL)

    assert captured["path"] == "/v1/api/jobs/gb/search/1"


@pytest.mark.usefixtures("credentials")
def test_fetch_jobs_timeout_raises_adzuna_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("connection timed out", request=request)

    with make_client(handler) as client:
        with pytest.raises(AdzunaError, match="Timeout"):
            fetch_jobs(client=client, api_url=API_URL)


@pytest.mark.parametrize("status_code", [401, 429, 500])
@pytest.mark.usefixtures("credentials")
def test_fetch_jobs_error_status_raises_adzuna_error(status_code: int) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        # 401 mirrors the real AUTH_FAIL body returned for bad credentials.
        body = {"exception": "AUTH_FAIL", "display": "Authorisation failed"}
        return httpx.Response(status_code, json=body)

    with make_client(handler) as client:
        with pytest.raises(AdzunaError, match=f"HTTP {status_code}"):
            fetch_jobs(client=client, api_url=API_URL)


@pytest.mark.usefixtures("credentials")
def test_fetch_jobs_non_json_body_raises_adzuna_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"<html>definitely not JSON</html>")

    with make_client(handler) as client:
        with pytest.raises(AdzunaError, match="non-JSON"):
            fetch_jobs(client=client, api_url=API_URL)


@pytest.mark.usefixtures("credentials")
def test_fetch_jobs_unexpected_shape_raises_adzuna_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"detail": "no results list here"})

    with make_client(handler) as client:
        with pytest.raises(AdzunaError, match="Unexpected shape"):
            fetch_jobs(client=client, api_url=API_URL)


@pytest.mark.usefixtures("credentials")
def test_fetch_tech_jobs_consolidates_pages_and_stops_on_short_page() -> None:
    pages = {1: PAGE_1, 2: PAGE_2}
    requested: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        page = int(request.url.path.rsplit("/", 1)[-1])
        requested.append(page)
        return httpx.Response(200, json=pages[page])

    with make_client(handler) as client:
        payload = fetch_tech_jobs(
            max_pages=5, results_per_page=2, client=client, api_url=API_URL
        )

    # Page 2 is short (1 < 2 results), so page 3 is never requested.
    assert requested == [1, 2]
    assert payload["job-count"] == 3
    assert [job["id"] for job in payload["jobs"]] == [1, 2, 3]
    assert payload["fetched-pages"] == [1, 2]
    assert payload["failed-pages"] == {}
    assert payload["country"] == "gb"


@pytest.mark.usefixtures("credentials")
def test_fetch_tech_jobs_dedups_repeated_ids_across_pages() -> None:
    duplicated = {"count": 4, "results": PAGE_1["results"]}

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=duplicated)

    with make_client(handler) as client:
        payload = fetch_tech_jobs(
            max_pages=2, results_per_page=2, client=client, api_url=API_URL
        )

    assert payload["job-count"] == 2
    assert [job["id"] for job in payload["jobs"]] == [1, 2]


@pytest.mark.usefixtures("credentials")
def test_fetch_tech_jobs_partial_failure_keeps_other_pages() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        page = int(request.url.path.rsplit("/", 1)[-1])
        if page == 1:
            return httpx.Response(500)
        return httpx.Response(200, json=PAGE_2)

    with make_client(handler) as client:
        payload = fetch_tech_jobs(
            max_pages=2, results_per_page=2, client=client, api_url=API_URL
        )

    assert payload["job-count"] == 1
    assert [job["id"] for job in payload["jobs"]] == [3]
    assert payload["fetched-pages"] == [2]
    assert set(payload["failed-pages"]) == {"1"}
    assert "HTTP 500" in payload["failed-pages"]["1"]


@pytest.mark.usefixtures("credentials")
def test_fetch_tech_jobs_all_pages_failing_raises_adzuna_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    with make_client(handler) as client:
        with pytest.raises(AdzunaError, match="All pages failed"):
            fetch_tech_jobs(max_pages=3, client=client, api_url=API_URL)
