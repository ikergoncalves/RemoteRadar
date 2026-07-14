"""Tests for the Remotive extraction (HTTP mocked via httpx.MockTransport)."""

from __future__ import annotations

from collections.abc import Callable

import httpx
import pytest

from remoteradar.extract.remotive import RemotiveError, fetch_jobs

API_URL = "https://api.test/remote-jobs"

FAKE_PAYLOAD = {
    "job-count": 2,
    "jobs": [
        {"id": 1, "title": "Data Engineer", "company_name": "Acme"},
        {"id": 2, "title": "Backend Developer", "company_name": "Globex"},
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
        fetch_jobs(search="python", limit=5, client=client, api_url=API_URL)

    assert captured == {"category": "software-dev", "search": "python", "limit": "5"}


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
