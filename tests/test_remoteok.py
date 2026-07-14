"""Tests for the RemoteOK extraction (HTTP mocked via httpx.MockTransport)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx
import pytest

from remoteradar.extract.remoteok import RemoteOKError, fetch_jobs, fetch_tech_jobs

API_URL = "https://api.test/remoteok"

# The real API prepends a legal-notice object (no "id") to the job array.
LEGAL_NOTICE = {"last_updated": 1784050634, "legal": "API Terms of Service: ..."}

JOBS_BY_TAG: dict[str, list[dict[str, Any]]] = {
    "dev": [
        {"id": "101", "position": "Backend Developer", "tags": ["dev", "backend"]},
        {"id": "102", "position": "Full Stack Engineer", "tags": ["dev", "engineer"]},
    ],
    "engineer": [
        {"id": "102", "position": "Full Stack Engineer", "tags": ["dev", "engineer"]},
        {"id": "103", "position": "Platform Engineer", "tags": ["engineer", "devops"]},
    ],
}

TEST_TAGS = ("dev", "engineer")


def make_client(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_fetch_jobs_success_strips_legal_notice() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[LEGAL_NOTICE, *JOBS_BY_TAG["dev"]])

    with make_client(handler) as client:
        jobs = fetch_jobs(client=client, api_url=API_URL)

    assert jobs == JOBS_BY_TAG["dev"]


def test_fetch_jobs_sends_tags_param() -> None:
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(dict(request.url.params))
        return httpx.Response(200, json=[LEGAL_NOTICE])

    with make_client(handler) as client:
        fetch_jobs(tag="data science", client=client, api_url=API_URL)

    assert captured == {"tags": "data science"}


def test_fetch_jobs_timeout_raises_remoteok_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("connection timed out", request=request)

    with make_client(handler) as client:
        with pytest.raises(RemoteOKError, match="Timeout"):
            fetch_jobs(client=client, api_url=API_URL)


@pytest.mark.parametrize("status_code", [404, 429, 500])
def test_fetch_jobs_error_status_raises_remoteok_error(status_code: int) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code)

    with make_client(handler) as client:
        with pytest.raises(RemoteOKError, match=f"HTTP {status_code}"):
            fetch_jobs(client=client, api_url=API_URL)


def test_fetch_jobs_non_json_body_raises_remoteok_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"<html>definitely not JSON</html>")

    with make_client(handler) as client:
        with pytest.raises(RemoteOKError, match="non-JSON"):
            fetch_jobs(client=client, api_url=API_URL)


@pytest.mark.parametrize("body", [{"jobs": []}, ["not-an-object"]])
def test_fetch_jobs_unexpected_shape_raises_remoteok_error(body: Any) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=body)

    with make_client(handler) as client:
        with pytest.raises(RemoteOKError, match="Unexpected shape"):
            fetch_jobs(client=client, api_url=API_URL)


def test_fetch_tech_jobs_aggregates_and_dedups_across_tags() -> None:
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        tag = request.url.params["tags"]
        requested.append(tag)
        return httpx.Response(200, json=[LEGAL_NOTICE, *JOBS_BY_TAG[tag]])

    with make_client(handler) as client:
        payload = fetch_tech_jobs(TEST_TAGS, client=client, api_url=API_URL)

    assert requested == ["dev", "engineer"]
    # Job 102 appears in both responses and must be kept only once.
    assert payload["job-count"] == 3
    assert [job["id"] for job in payload["jobs"]] == ["101", "102", "103"]
    assert payload["fetched-tags"] == ["dev", "engineer"]
    assert payload["failed-tags"] == {}


def test_fetch_tech_jobs_drops_jobs_without_the_requested_tag() -> None:
    # The server-side tag filter is not strict: responses can include jobs
    # lacking the requested tag. Those must be filtered out client-side.
    def handler(request: httpx.Request) -> httpx.Response:
        jobs = JOBS_BY_TAG["dev"] + [
            {"id": "999", "position": "Recruiter", "tags": ["recruiter", "non tech"]},
            {"id": "998", "position": "No tags at all"},
        ]
        return httpx.Response(200, json=[LEGAL_NOTICE, *jobs])

    with make_client(handler) as client:
        payload = fetch_tech_jobs(("dev",), client=client, api_url=API_URL)

    assert payload["job-count"] == 2
    assert [job["id"] for job in payload["jobs"]] == ["101", "102"]


def test_fetch_tech_jobs_partial_failure_keeps_other_tags() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        tag = request.url.params["tags"]
        if tag == "dev":
            return httpx.Response(500)
        return httpx.Response(200, json=[LEGAL_NOTICE, *JOBS_BY_TAG[tag]])

    with make_client(handler) as client:
        payload = fetch_tech_jobs(TEST_TAGS, client=client, api_url=API_URL)

    assert payload["job-count"] == 2
    assert [job["id"] for job in payload["jobs"]] == ["102", "103"]
    assert payload["fetched-tags"] == ["engineer"]
    assert set(payload["failed-tags"]) == {"dev"}
    assert "HTTP 500" in payload["failed-tags"]["dev"]


def test_fetch_tech_jobs_all_tags_failing_raises_remoteok_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    with make_client(handler) as client:
        with pytest.raises(RemoteOKError, match="All tags failed"):
            fetch_tech_jobs(TEST_TAGS, client=client, api_url=API_URL)
