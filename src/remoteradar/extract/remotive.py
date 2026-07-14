"""Extraction of remote job postings from the Remotive public API.

API reference: https://remotive.com/api/remote-jobs
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from remoteradar.config import remotive_api_url

DEFAULT_TIMEOUT_SECONDS = 30.0
DEFAULT_CATEGORY = "software-dev"


class RemotiveError(Exception):
    """Raised when the Remotive API request fails or returns malformed data."""


def fetch_jobs(
    category: str | None = DEFAULT_CATEGORY,
    search: str | None = None,
    limit: int | None = None,
    *,
    api_url: str | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    client: httpx.Client | None = None,
) -> dict[str, Any]:
    """Fetch job postings from Remotive and return the raw JSON payload untouched.

    Args:
        category: Remotive category slug to filter by (default: tech jobs).
            Pass ``None`` to fetch all categories.
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
        raise RemotiveError(f"Timeout apos {timeout}s ao chamar {url}") from exc
    except httpx.HTTPStatusError as exc:
        raise RemotiveError(
            f"API da Remotive retornou HTTP {exc.response.status_code} para {url}"
        ) from exc
    except httpx.HTTPError as exc:
        raise RemotiveError(f"Falha na requisicao HTTP para {url}: {exc}") from exc
    except (json.JSONDecodeError, ValueError) as exc:
        raise RemotiveError(f"API da Remotive retornou resposta nao-JSON de {url}") from exc
    finally:
        if owns_client:
            http.close()

    if not isinstance(payload, dict) or not isinstance(payload.get("jobs"), list):
        raise RemotiveError(
            "Formato inesperado na resposta da Remotive: esperado objeto com lista 'jobs'"
        )
    return payload


def main() -> None:
    """Fetch tech jobs from Remotive and store the raw payload in PostgreSQL."""
    from remoteradar.config import load_env
    from remoteradar.load import insert_raw_remotive_payload

    load_env()
    payload = fetch_jobs()
    row_id = insert_raw_remotive_payload(payload)
    print(
        f"Payload bruto da Remotive salvo em raw.remotive_jobs (id={row_id}, "
        f"{len(payload['jobs'])} vagas)."
    )


if __name__ == "__main__":
    main()
