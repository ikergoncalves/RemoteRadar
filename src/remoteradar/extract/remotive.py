"""Extraction of remote job postings from the Remotive public API.

API reference: https://remotive.com/api/remote-jobs
Category slugs/names: https://remotive.com/api/remote-jobs/categories
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from typing import Any

import httpx

from remoteradar.config import remotive_api_url

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_SECONDS = 30.0

# Slug -> display name, as listed at https://remotive.com/api/remote-jobs/categories.
# Tech categories relevant to language/salary/company trends; excludes non-tech
# ones (marketing, sales, medical, ...) and ambiguous ones (engineering, design,
# product), which mix in jobs outside the project's scope.
TECH_CATEGORIES: dict[str, str] = {
    "software-development": "Software Development",
    "artificial-intelligence": "Artificial Intelligence",
    "data": "Data and Analytics",
    "devops": "Devops",
    "qa": "Quality Assurance",
    "information-technology": "Information Technology",
}


class RemotiveError(Exception):
    """Raised when the Remotive API request fails or returns malformed data."""


def fetch_jobs(
    category: str | None = None,
    search: str | None = None,
    limit: int | None = None,
    *,
    api_url: str | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    client: httpx.Client | None = None,
) -> dict[str, Any]:
    """Fetch job postings from Remotive and return the raw JSON payload untouched.

    Args:
        category: Remotive category slug to filter by; ``None`` fetches all
            categories. For the consolidated multi-category extraction use
            :func:`fetch_tech_jobs`.
        search: optional free-text search term.
        limit: optional maximum number of jobs returned by the API.
        api_url: overrides the endpoint (defaults to REMOTIVE_API_URL or the public URL).
        timeout: request timeout in seconds, applied when no ``client`` is given.
        client: optional pre-configured ``httpx.Client`` (used by tests via MockTransport).

    Raises:
        RemotiveError: on timeout, HTTP error status, non-JSON body or
            unexpected response shape.
    """
    url = api_url or remotive_api_url()
    params: dict[str, str | int] = {}
    if category:
        params["category"] = category
    if search:
        params["search"] = search
    if limit is not None:
        params["limit"] = limit

    owns_client = client is None
    http = client if client is not None else httpx.Client(timeout=timeout)
    try:
        response = http.get(url, params=params)
        response.raise_for_status()
        payload = response.json()
    except httpx.TimeoutException as exc:
        raise RemotiveError(f"Timeout after {timeout}s while calling {url}") from exc
    except httpx.HTTPStatusError as exc:
        raise RemotiveError(
            f"Remotive API returned HTTP {exc.response.status_code} for {url}"
        ) from exc
    except httpx.HTTPError as exc:
        raise RemotiveError(f"HTTP request to {url} failed: {exc}") from exc
    except (json.JSONDecodeError, ValueError) as exc:
        raise RemotiveError(f"Remotive API returned a non-JSON response from {url}") from exc
    finally:
        if owns_client:
            http.close()

    if not isinstance(payload, dict) or not isinstance(payload.get("jobs"), list):
        raise RemotiveError(
            "Unexpected shape in Remotive response: expected an object with a 'jobs' list"
        )
    return payload


def fetch_tech_jobs(
    categories: Mapping[str, str] = TECH_CATEGORIES,
    *,
    api_url: str | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    client: httpx.Client | None = None,
) -> dict[str, Any]:
    """Fetch all tech categories from Remotive and consolidate into one payload.

    Makes one call per category (the API's documented contract) and aggregates
    the results. The API has been ignoring the ``category`` filter server-side
    and returning the full feed on every call, so each response goes through a
    client-side filter on the category name plus deduplication by ``id`` —
    without this the consolidated payload would contain non-tech jobs and
    repeated copies.

    Partial failure: a failing category is logged and recorded under
    ``failed-categories``, and the extraction continues with the remaining
    ones — in a daily trends pipeline, partial data is worth more than no
    data, and the gap stays auditable in the warehouse. If all of them fail,
    raises :class:`RemotiveError`, since there is nothing to load.

    Args:
        categories: mapping of category slug -> display name in the API
            (default: :data:`TECH_CATEGORIES`).
        api_url: overrides the endpoint (defaults to REMOTIVE_API_URL or the public URL).
        timeout: request timeout in seconds, applied when no ``client`` is given.
        client: optional pre-configured ``httpx.Client`` (used by tests via MockTransport).

    Returns:
        Consolidated payload: ``{"job-count", "jobs", "fetched-categories",
        "failed-categories"}``.

    Raises:
        RemotiveError: if all categories fail.
    """
    jobs: list[dict[str, Any]] = []
    seen_ids: set[Any] = set()
    fetched: list[str] = []
    failed: dict[str, str] = {}
    expected_names = {name.casefold(): slug for slug, name in categories.items()}

    owns_client = client is None
    http = client if client is not None else httpx.Client(timeout=timeout)
    try:
        for slug in categories:
            try:
                payload = fetch_jobs(category=slug, api_url=api_url, client=http)
            except RemotiveError as exc:
                logger.warning("Category %r failed, continuing with the rest: %s", slug, exc)
                failed[slug] = str(exc)
                continue
            fetched.append(slug)
            for job in payload["jobs"]:
                job_category = str(job.get("category", "")).casefold()
                if expected_names.get(job_category) != slug:
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
        raise RemotiveError(
            "All categories failed during the Remotive extraction: "
            + "; ".join(f"{slug}: {msg}" for slug, msg in failed.items())
        )
    return {
        "job-count": len(jobs),
        "jobs": jobs,
        "fetched-categories": fetched,
        "failed-categories": failed,
    }


def main() -> None:
    """Fetch tech jobs from Remotive and store the raw payload in PostgreSQL."""
    import sys

    from remoteradar.config import ConfigError, database_url, load_env
    from remoteradar.load import insert_raw_payload

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    load_env()
    try:
        dsn = database_url()  # fail fast: validate config before calling the API
        payload = fetch_tech_jobs()
        row_id = insert_raw_payload("remotive", payload, dsn=dsn)
    except (ConfigError, RemotiveError) as exc:
        sys.exit(f"Error: {exc}")
    summary = (
        f"Consolidated Remotive payload stored in raw.remotive_jobs (id={row_id}, "
        f"{payload['job-count']} jobs from {len(payload['fetched-categories'])} categories)."
    )
    if payload["failed-categories"]:
        summary += f" Failed categories: {', '.join(payload['failed-categories'])}."
    print(summary)


if __name__ == "__main__":
    main()
