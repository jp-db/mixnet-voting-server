"""Settings, read from the environment (prefix ``BALLOT_BOX_``) or a ``.env`` file."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="BALLOT_BOX_", env_file=".env", extra="ignore")

    secret_key: SecretStr
    """Signs session cookies and CSRF tokens. Required; no default on purpose."""

    database_url: str = "postgresql+psycopg://voting:voting@localhost:5432/voting"
    redis_url: str = "redis://localhost:6379/0"
    blob_dir: Path = Path("data/blobs")

    public_url: str = "http://localhost:8000"
    """Base URL voters reach; used for CAS/OIDC callback URLs."""

    auth_providers: list[str] = ["password"]
    """Enabled login methods, in display order: ``password``, ``cas``, ``oidc``."""
    cas_server_url: str | None = None


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]  # filled from the environment
