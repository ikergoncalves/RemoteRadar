"""Configuration read from environment variables (with .env support)."""

from __future__ import annotations

import os

from dotenv import load_dotenv

DEFAULT_REMOTIVE_API_URL = "https://remotive.com/api/remote-jobs"
DEFAULT_REMOTEOK_API_URL = "https://remoteok.com/api"
DEFAULT_ADZUNA_API_URL = "https://api.adzuna.com/v1/api/jobs"
# Default country for Adzuna job searches. "gb" is Adzuna's home market and
# has the deepest job coverage; override with ADZUNA_COUNTRY (e.g. "us").
DEFAULT_ADZUNA_COUNTRY = "gb"


class ConfigError(RuntimeError):
    """Raised when a required environment variable is missing."""


def load_env() -> None:
    """Load variables from a local .env file into the environment, if present."""
    load_dotenv()


def remotive_api_url() -> str:
    """Base URL of the Remotive API (REMOTIVE_API_URL, with a public default)."""
    return os.environ.get("REMOTIVE_API_URL") or DEFAULT_REMOTIVE_API_URL


def remoteok_api_url() -> str:
    """Base URL of the RemoteOK API (REMOTEOK_API_URL, with a public default)."""
    return os.environ.get("REMOTEOK_API_URL") or DEFAULT_REMOTEOK_API_URL


def adzuna_api_url() -> str:
    """Base URL of the Adzuna API (ADZUNA_API_URL, with a public default)."""
    return os.environ.get("ADZUNA_API_URL") or DEFAULT_ADZUNA_API_URL


def adzuna_country() -> str:
    """Country code for Adzuna job searches (ADZUNA_COUNTRY, default ``gb``)."""
    return os.environ.get("ADZUNA_COUNTRY") or DEFAULT_ADZUNA_COUNTRY


def adzuna_credentials() -> tuple[str, str]:
    """Adzuna API credentials from ADZUNA_APP_ID and ADZUNA_APP_KEY.

    Raises:
        ConfigError: if either variable is missing, with instructions on how
            to fix it.
    """
    app_id = os.environ.get("ADZUNA_APP_ID")
    app_key = os.environ.get("ADZUNA_APP_KEY")
    if not app_id or not app_key:
        raise ConfigError(
            "ADZUNA_APP_ID and/or ADZUNA_APP_KEY are not set. Register a free application "
            "at https://developer.adzuna.com/, then copy .env.example to .env and fill in "
            "both values."
        )
    return app_id, app_key


def database_url() -> str:
    """PostgreSQL connection string from DATABASE_URL.

    Raises:
        ConfigError: if DATABASE_URL is not set, with instructions on how to fix it.
    """
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise ConfigError(
            "DATABASE_URL is not set. Copy .env.example to .env and fill in the "
            "PostgreSQL connection string "
            "(format: postgresql://user:password@host:5432/database_name)."
        )
    return url
