"""Extraction of job postings from the Adzuna job search API.

API reference: https://developer.adzuna.com/docs/search

Endpoint shape: ``GET {base}/{country}/search/{page}`` with ``app_id`` and
``app_key`` as query parameters (free registration at
https://developer.adzuna.com/). The response is a JSON object with a
``results`` list; each result carries ``id``, ``title``,
``company.display_name``, ``location.display_name``, ``salary_min``,
``salary_max``, ``salary_is_predicted``, ``created``, ``redirect_url``,
``category.label``/``category.tag``, ``contract_type`` and a ``description``
snippet. Tech jobs are selected server-side with ``category=it-jobs`` (the
"IT Jobs" category from the categories endpoint). Invalid credentials yield
HTTP 401 with an ``AUTH_FAIL`` JSON body.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx

from remoteradar.config import adzuna_api_url, adzuna_country, adzuna_credentials

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_SECONDS = 30.0

# Adzuna category tag for the "IT Jobs" category (see /v1/api/jobs/{country}/categories).
IT_JOBS_CATEGORY = "it-jobs"

# 50 is the maximum the search endpoint returns per page.
DEFAULT_RESULTS_PER_PAGE = 50

# Pages fetched by fetch_tech_jobs: 5 pages x 50 results covers the most
# recent postings while staying well inside the free-tier call quota.
DEFAULT_MAX_PAGES = 5


class AdzunaError(Exception):
    """Raised when the Adzuna API request fails or returns malformed data."""


def fetch_jobs(
    page: int = 1,
    *,
    country: str | None = None,
    category: str | None = IT_JOBS_CATEGORY,
    what: str | None = None,
    results_per_page: int = DEFAULT_RESULTS_PER_PAGE,
    api_url: str | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    client: httpx.Client | None = None,
) -> dict[str, Any]:
    """Fetch one page of Adzuna job search results, returning the raw payload.

    Credentials are read from ADZUNA_APP_ID / ADZUNA_APP_KEY and validated
    before any HTTP call is made.

    Args:
        page: 1-based results page.
        country: two-letter country code for the search
            (defaults to ADZUNA_COUNTRY or ``gb``).
        category: Adzuna category tag; defaults to :data:`IT_JOBS_CATEGORY`.
            Pass ``None`` to search across all categories.
        what: optional free-text search term.
        results_per_page: page size (the API caps it at 50).
        api_url: overrides the API base URL (defaults to ADZUNA_API_URL or the
            public URL).
        timeout: request timeout in seconds, applied when no ``client`` is given.
        client: optional pre-configured ``httpx.Client`` (used by tests via MockTransport).

    Raises:
        ConfigError: if ADZUNA_APP_ID or ADZUNA_APP_KEY is missing.
        AdzunaError: on timeout, HTTP error status (including 401 for bad
            credentials), non-JSON body or unexpected response shape.
    """
    app_id, app_key = adzuna_credentials()  # fail fast, before any HTTP call
    base = (api_url or adzuna_api_url()).rstrip("/")
    url = f"{base}/{country or adzuna_country()}/search/{page}"
    params: dict[str, str | int] = {
        "app_id": app_id,
        "app_key": app_key,
        "results_per_page": results_per_page,
    }
    if category:
        params["category"] = category
    if what:
        params["what"] = what

    owns_client = client is None
    http = client if client is not None else httpx.Client(timeout=timeout)
    try:
        response = http.get(url, params=params)
        response.raise_for_status()
        payload = response.json()
    except httpx.TimeoutException as exc:
        raise AdzunaError(f"Timeout after {timeout}s while calling {url}") from exc
    except httpx.HTTPStatusError as exc:
        raise AdzunaError(
            f"Adzuna API returned HTTP {exc.response.status_code} for {url}"
        ) from exc
    except httpx.HTTPError as exc:
        raise AdzunaError(f"HTTP request to {url} failed: {exc}") from exc
    except (json.JSONDecodeError, ValueError) as exc:
        raise AdzunaError(f"Adzuna API returned a non-JSON response from {url}") from exc
    finally:
        if owns_client:
            http.close()

    if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
        raise AdzunaError(
            "Unexpected shape in Adzuna response: expected an object with a 'results' list"
        )
    return payload


def fetch_tech_jobs(
    max_pages: int = DEFAULT_MAX_PAGES,
    *,
    country: str | None = None,
    results_per_page: int = DEFAULT_RESULTS_PER_PAGE,
    api_url: str | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    client: httpx.Client | None = None,
) -> dict[str, Any]:
    """Fetch IT jobs from Adzuna across pages and consolidate into one payload.

    Pages through the ``it-jobs`` category (which Adzuna filters server-side)
    and aggregates the results, deduplicating by ``id``. Paging stops early
    when a page comes back with fewer results than requested — there is
    nothing left to fetch.

    Partial failure: a failing page is logged and recorded under
    ``failed-pages``, and the extraction continues with the remaining ones.
    If all pages fail, raises :class:`AdzunaError`, since there is nothing
    to load.

    Args:
        max_pages: maximum number of pages to fetch.
        country: two-letter country code for the search
            (defaults to ADZUNA_COUNTRY or ``gb``).
        results_per_page: page size (the API caps it at 50).
        api_url: overrides the API base URL (defaults to ADZUNA_API_URL or the
            public URL).
        timeout: request timeout in seconds, applied when no ``client`` is given.
        client: optional pre-configured ``httpx.Client`` (used by tests via MockTransport).

    Returns:
        Consolidated payload: ``{"job-count", "jobs", "country",
        "fetched-pages", "failed-pages"}`` (same shape as the other sources'
        consolidated payloads, with Adzuna results under ``jobs``).

    Raises:
        ConfigError: if ADZUNA_APP_ID or ADZUNA_APP_KEY is missing.
        AdzunaError: if all pages fail.
    """
    adzuna_credentials()  # fail fast, before any HTTP call
    search_country = country or adzuna_country()
    jobs: list[dict[str, Any]] = []
    seen_ids: set[Any] = set()
    fetched: list[int] = []
    failed: dict[str, str] = {}

    owns_client = client is None
    http = client if client is not None else httpx.Client(timeout=timeout)
    try:
        for page in range(1, max_pages + 1):
            try:
                payload = fetch_jobs(
                    page,
                    country=search_country,
                    results_per_page=results_per_page,
                    api_url=api_url,
                    client=http,
                )
            except AdzunaError as exc:
                logger.warning("Page %d failed, continuing with the rest: %s", page, exc)
                failed[str(page)] = str(exc)
                continue
            fetched.append(page)
            results = payload["results"]
            for job in results:
                job_id = job.get("id")
                if job_id is not None:
                    if job_id in seen_ids:
                        continue
                    seen_ids.add(job_id)
                jobs.append(job)
            if len(results) < results_per_page:
                break
    finally:
        if owns_client:
            http.close()

    if not fetched:
        raise AdzunaError(
            "All pages failed during the Adzuna extraction: "
            + "; ".join(f"page {page}: {msg}" for page, msg in failed.items())
        )
    return {
        "job-count": len(jobs),
        "jobs": jobs,
        "country": search_country,
        "fetched-pages": fetched,
        "failed-pages": failed,
    }


def main() -> None:
    """Fetch IT jobs from Adzuna and store the raw payload in PostgreSQL."""
    import sys

    from remoteradar.config import ConfigError, database_url, load_env
    from remoteradar.load import insert_raw_payload

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    load_env()
    try:
        dsn = database_url()  # fail fast: validate config before calling the API
        payload = fetch_tech_jobs()
        row_id = insert_raw_payload("adzuna", payload, dsn=dsn)
    except (ConfigError, AdzunaError) as exc:
        sys.exit(f"Error: {exc}")
    summary = (
        f"Consolidated Adzuna payload stored in raw.adzuna_jobs (id={row_id}, "
        f"{payload['job-count']} jobs from {len(payload['fetched-pages'])} pages, "
        f"country={payload['country']})."
    )
    if payload["failed-pages"]:
        summary += f" Failed pages: {', '.join(payload['failed-pages'])}."
    print(summary)


if __name__ == "__main__":
    main()
