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

# Slug -> nome exibido, conforme https://remotive.com/api/remote-jobs/categories.
# Categorias tech relevantes para tendencias de linguagens/salarios/empresas;
# exclui as nao-tech (marketing, vendas, medical, ...) e as ambiguas (engineering,
# design, product), que misturam vagas fora do escopo do projeto.
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


def fetch_tech_jobs(
    categories: Mapping[str, str] = TECH_CATEGORIES,
    *,
    api_url: str | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    client: httpx.Client | None = None,
) -> dict[str, Any]:
    """Fetch all tech categories from Remotive and consolidate into one payload.

    Faz uma chamada por categoria (contrato documentado da API) e agrega os
    resultados. A API vem ignorando o filtro ``category`` no servidor e
    devolvendo o feed completo em toda chamada, entao cada resposta passa por
    um filtro client-side pelo nome da categoria e por deduplicacao por ``id``
    — sem isso o payload consolidado teria vagas nao-tech e copias repetidas.

    Falha parcial: uma categoria que falha e logada e registrada em
    ``failed-categories``, e a extracao segue com as demais — num pipeline
    diario de tendencias, dados parciais valem mais que nenhum dado, e a
    lacuna fica auditavel no warehouse. Se todas falharem, levanta
    :class:`RemotiveError`, pois nao ha o que carregar.

    Args:
        categories: mapping de slug -> nome exibido da categoria na API
            (default: :data:`TECH_CATEGORIES`).
        api_url: overrides the endpoint (defaults to REMOTIVE_API_URL or the public URL).
        timeout: request timeout in seconds, applied when no ``client`` is given.
        client: optional pre-configured ``httpx.Client`` (used by tests via MockTransport).

    Returns:
        Payload consolidado: ``{"job-count", "jobs", "fetched-categories",
        "failed-categories"}``.

    Raises:
        RemotiveError: se todas as categorias falharem.
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
                logger.warning("Categoria %r falhou, seguindo com as demais: %s", slug, exc)
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
            "Todas as categorias falharam na extracao da Remotive: "
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
    from remoteradar.load import insert_raw_remotive_payload

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    load_env()
    try:
        dsn = database_url()  # fail fast: valida a config antes de chamar a API
        payload = fetch_tech_jobs()
        row_id = insert_raw_remotive_payload(payload, dsn=dsn)
    except (ConfigError, RemotiveError) as exc:
        sys.exit(f"Erro: {exc}")
    summary = (
        f"Payload consolidado da Remotive salvo em raw.remotive_jobs (id={row_id}, "
        f"{payload['job-count']} vagas de {len(payload['fetched-categories'])} categorias)."
    )
    if payload["failed-categories"]:
        summary += f" Categorias com falha: {', '.join(payload['failed-categories'])}."
    print(summary)


if __name__ == "__main__":
    main()
