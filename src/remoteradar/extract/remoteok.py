"""Extraction of remote job postings from the RemoteOK public API.

API reference: https://remoteok.com/api

The response is a JSON array whose first element is a legal-notice/metadata
object (keys ``legal`` and ``last_updated``); the remaining elements are job
objects with the fields ``id``, ``slug``, ``position``, ``company``, ``tags``,
``location``, ``salary_min``, ``salary_max``, ``date``, ``epoch``, ``url``,
``apply_url``, ``description``, ``logo`` and ``company_logo``. Numeric-looking
fields (``id``, ``epoch``, ``salary_min``, ``salary_max``) arrive as strings.
Each response is capped at roughly 100 jobs.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable
from typing import Any

import httpx

from remoteradar.config import remoteok_api_url

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_SECONDS = 30.0

# Tags used to cover the tech job space. The API accepts ?tags=<tag> and
# mostly honours it server-side, but not strictly (observed: ~10% of the
# results for some tags lack the requested tag), so fetch_tech_jobs also
# applies a client-side filter. One request per tag: responses are capped at
# ~100 jobs, so separate requests widen coverage; overlap is removed by
# deduplication on id.
TECH_TAGS: tuple[str, ...] = (
    "dev",
    "engineer",
    "devops",
    "data science",
    "backend",
    "front end",
    "sys admin",
    "testing",
)


class RemoteOKError(Exception):
    """Raised when the RemoteOK API request fails or returns malformed data."""


def fetch_jobs(
    tag: str | None = None,
    *,
    api_url: str | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    client: httpx.Client | None = None,
) -> list[dict[str, Any]]:
    """Fetch job postings from RemoteOK, returning the job objects untouched.

    The leading legal-notice element of the response array is stripped (it has
    no ``id``); the job objects themselves are returned as-is.

    Args:
        tag: RemoteOK tag to filter by (``?tags=<tag>``); ``None`` fetches the
            unfiltered feed. For the consolidated multi-tag extraction use
            :func:`fetch_tech_jobs`.
        api_url: overrides the endpoint (defaults to REMOTEOK_API_URL or the public URL).
        timeout: request timeout in seconds, applied when no ``client`` is given.
        client: optional pre-configured ``httpx.Client`` (used by tests via MockTransport).

    Raises:
        RemoteOKError: on timeout, HTTP error status, non-JSON body or
            unexpected response shape.
    """
    url = api_url or remoteok_api_url()
    params: dict[str, str] = {}
    if tag:
        params["tags"] = tag

    owns_client = client is None
    http = client if client is not None else httpx.Client(timeout=timeout)
    try:
        response = http.get(url, params=params)
        response.raise_for_status()
        payload = response.json()
    except httpx.TimeoutException as exc:
        raise RemoteOKError(f"Timeout after {timeout}s while calling {url}") from exc
    except httpx.HTTPStatusError as exc:
        raise RemoteOKError(
            f"RemoteOK API returned HTTP {exc.response.status_code} for {url}"
        ) from exc
    except httpx.HTTPError as exc:
        raise RemoteOKError(f"HTTP request to {url} failed: {exc}") from exc
    except (json.JSONDecodeError, ValueError) as exc:
        raise RemoteOKError(f"RemoteOK API returned a non-JSON response from {url}") from exc
    finally:
        if owns_client:
            http.close()

    if not isinstance(payload, list) or not all(isinstance(item, dict) for item in payload):
        raise RemoteOKError(
            "Unexpected shape in RemoteOK response: expected an array of objects"
        )
    # The first element is a legal notice without an id; everything else is a job.
    return [item for item in payload if "id" in item]


def fetch_tech_jobs(
    tags: Iterable[str] = TECH_TAGS,
    *,
    api_url: str | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    client: httpx.Client | None = None,
) -> dict[str, Any]:
    """Fetch all tech tags from RemoteOK and consolidate into one payload.

    Makes one call per tag and aggregates the results. The server-side tag
    filter is not strict, so each response goes through a client-side filter
    requiring the tag, plus deduplication by ``id`` — per-tag responses
    overlap heavily.

    Partial failure: a failing tag is logged and recorded under
    ``failed-tags``, and the extraction continues with the remaining ones.
    If all of them fail, raises :class:`RemoteOKError`, since there is
    nothing to load.

    Args:
        tags: RemoteOK tags to fetch (default: :data:`TECH_TAGS`).
        api_url: overrides the endpoint (defaults to REMOTEOK_API_URL or the public URL).
        timeout: request timeout in seconds, applied when no ``client`` is given.
        client: optional pre-configured ``httpx.Client`` (used by tests via MockTransport).

    Returns:
        Consolidated payload: ``{"job-count", "jobs", "fetched-tags",
        "failed-tags"}`` (same shape as the Remotive consolidated payload).

    Raises:
        RemoteOKError: if all tags fail.
    """
    jobs: list[dict[str, Any]] = []
    seen_ids: set[Any] = set()
    fetched: list[str] = []
    failed: dict[str, str] = {}

    owns_client = client is None
    http = client if client is not None else httpx.Client(timeout=timeout)
    try:
        for tag in tags:
            try:
                tag_jobs = fetch_jobs(tag=tag, api_url=api_url, client=http)
            except RemoteOKError as exc:
                logger.warning("Tag %r failed, continuing with the rest: %s", tag, exc)
                failed[tag] = str(exc)
                continue
            fetched.append(tag)
            wanted = tag.casefold()
            for job in tag_jobs:
                job_tags = job.get("tags")
                if not isinstance(job_tags, list):
                    continue
                if wanted not in (str(t).casefold() for t in job_tags):
                    continue
                job_id = job.get("id")
                if job_id is not None:
                    if job_id in seen_ids:
                        continue
                    seen_ids.add(job_id)
                jobs.append(job)
    finally:
        if owns_client:
            http.close()

    if not fetched:
        raise RemoteOKError(
            "All tags failed during the RemoteOK extraction: "
            + "; ".join(f"{tag}: {msg}" for tag, msg in failed.items())
        )
    return {
        "job-count": len(jobs),
        "jobs": jobs,
        "fetched-tags": fetched,
        "failed-tags": failed,
    }


def main() -> None:
    """Fetch tech jobs from RemoteOK and store the raw payload in PostgreSQL."""
    import sys

    from remoteradar.config import ConfigError, database_url, load_env
    from remoteradar.load import insert_raw_payload

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    load_env()
    try:
        dsn = database_url()  # fail fast: validate config before calling the API
        payload = fetch_tech_jobs()
        row_id = insert_raw_payload("remoteok", payload, dsn=dsn)
    except (ConfigError, RemoteOKError) as exc:
        sys.exit(f"Error: {exc}")
    summary = (
        f"Consolidated RemoteOK payload stored in raw.remoteok_jobs (id={row_id}, "
        f"{payload['job-count']} jobs from {len(payload['fetched-tags'])} tags)."
    )
    if payload["failed-tags"]:
        summary += f" Failed tags: {', '.join(payload['failed-tags'])}."
    print(summary)


if __name__ == "__main__":
    main()
