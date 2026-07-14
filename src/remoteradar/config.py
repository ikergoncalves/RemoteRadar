"""Configuration read from environment variables (with .env support)."""

from __future__ import annotations

import os

from dotenv import load_dotenv

DEFAULT_REMOTIVE_API_URL = "https://remotive.com/api/remote-jobs"


class ConfigError(RuntimeError):
    """Raised when a required environment variable is missing."""


def load_env() -> None:
    """Load variables from a local .env file into the environment, if present."""
    load_dotenv()


def remotive_api_url() -> str:
    """Base URL of the Remotive API (REMOTIVE_API_URL, with a public default)."""
    return os.environ.get("REMOTIVE_API_URL") or DEFAULT_REMOTIVE_API_URL


def database_url() -> str:
    """PostgreSQL connection string from DATABASE_URL.

    Raises:
        ConfigError: if DATABASE_URL is not set, with instructions on how to fix it.
    """
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise ConfigError(
            "DATABASE_URL nao esta configurada. Copie .env.example para .env e preencha a "
            "string de conexao do PostgreSQL "
            "(formato: postgresql://usuario:senha@host:5432/nome_do_banco)."
        )
    return url
