"""Tests for the Remotive extraction (HTTP mocked via httpx.MockTransport)."""

from __future__ import annotations

from collections.abc import Callable

import httpx
import pytest

from remoteradar.extract.remotive import RemotiveError, fetch_jobs, fetch_tech_jobs

API_URL = "https://api.test/remote-jobs"

FAKE_PAYLOAD = {
    "job-count": 2,
    "jobs": [
        {"id": 1, "title": "Data Engineer", "company_name": "Acme"},
        {"id": 2, "title": "Backend Developer", "company_name": "Globex"},
    ],
}

TEST_CATEGORIES = {
    "software-development": "Software Development",
    "data": "Data and Analytics",
}

JOBS_BY_CATEGORY = {
    "software-development": [
        {"id": 1, "title": "Backend Developer", "category": "Software Development"},
        {"id": 2, "title": "Frontend Developer", "category": "Software Development"},
    ],
    "data": [
        {"id": 3, "title": "Data Engineer", "category": "Data and Analytics"},
    ],
}


def make_client(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_fetch_jobs_success_returns_raw_payload() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=FAKE_PAYLOAD)

    with make_client(handler) as client:
        payload = fetch_jobs(client=client, api_url=API_URL)

    assert payload == FAKE_PAYLOAD


def test_fetch_jobs_sends_query_params() -> None:
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(dict(request.url.params))
        return httpx.Response(200, json=FAKE_PAYLOAD)

    with make_client(handler) as client:
        fetch_jobs(category="devops", search="python", limit=5, client=client, api_url=API_URL)

    assert captured == {"category": "devops", "search": "python", "limit": "5"}


def test_fetch_jobs_timeout_raises_remotive_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("connection timed out", request=request)

    with make_client(handler) as client:
        with pytest.raises(RemotiveError, match="Timeout"):
            fetch_jobs(client=client, api_url=API_URL)


@pytest.mark.parametrize("status_code", [404, 429, 500])
def test_fetch_jobs_error_status_raises_remotive_error(status_code: int) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code)

    with make_client(handler) as client:
        with pytest.raises(RemotiveError, match=f"HTTP {status_code}"):
            fetch_jobs(client=client, api_url=API_URL)


def test_fetch_jobs_non_json_body_raises_remotive_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"<html>definitivamente nao e JSON</html>")

    with make_client(handler) as client:
        with pytest.raises(RemotiveError, match="nao-JSON"):
            fetch_jobs(client=client, api_url=API_URL)


def test_fetch_jobs_unexpected_shape_raises_remotive_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"detail": "sem lista de jobs aqui"})

    with make_client(handler) as client:
        with pytest.raises(RemotiveError, match="Formato inesperado"):
            fetch_jobs(client=client, api_url=API_URL)


def test_fetch_tech_jobs_aggregates_all_categories() -> None:
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        category = request.url.params["category"]
        requested.append(category)
        jobs = JOBS_BY_CATEGORY[category]
        return httpx.Response(200, json={"job-count": len(jobs), "jobs": jobs})

    with make_client(handler) as client:
        payload = fetch_tech_jobs(TEST_CATEGORIES, client=client, api_url=API_URL)

    assert requested == ["software-development", "data"]
    assert payload["job-count"] == 3
    assert [job["id"] for job in payload["jobs"]] == [1, 2, 3]
    assert payload["fetched-categories"] == ["software-development", "data"]
    assert payload["failed-categories"] == {}


def test_fetch_tech_jobs_filters_and_dedups_when_api_ignores_category() -> None:
    # A API da Remotive vem ignorando o filtro server-side e devolvendo o feed
    # completo em toda chamada; o consolidado deve manter so as categorias
    # pedidas, sem duplicar vagas entre as chamadas.
    full_feed = (
        JOBS_BY_CATEGORY["software-development"]
        + JOBS_BY_CATEGORY["data"]
        + [{"id": 99, "title": "Nurse", "category": "Medical"}]
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"job-count": len(full_feed), "jobs": full_feed})

    with make_client(handler) as client:
        payload = fetch_tech_jobs(TEST_CATEGORIES, client=client, api_url=API_URL)

    assert payload["job-count"] == 3
    assert [job["id"] for job in payload["jobs"]] == [1, 2, 3]
    assert all(job["category"] != "Medical" for job in payload["jobs"])
    assert payload["failed-categories"] == {}


def test_fetch_tech_jobs_partial_failure_keeps_other_categories() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        category = request.url.params["category"]
        if category == "software-development":
            return httpx.Response(500)
        jobs = JOBS_BY_CATEGORY[category]
        return httpx.Response(200, json={"job-count": len(jobs), "jobs": jobs})

    with make_client(handler) as client:
        payload = fetch_tech_jobs(TEST_CATEGORIES, client=client, api_url=API_URL)

    assert payload["job-count"] == 1
    assert [job["id"] for job in payload["jobs"]] == [3]
    assert payload["fetched-categories"] == ["data"]
    assert set(payload["failed-categories"]) == {"software-development"}
    assert "HTTP 500" in payload["failed-categories"]["software-development"]


def test_fetch_tech_jobs_all_categories_failing_raises_remotive_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    with make_client(handler) as client:
        with pytest.raises(RemotiveError, match="Todas as categorias falharam"):
            fetch_tech_jobs(TEST_CATEGORIES, client=client, api_url=API_URL)
