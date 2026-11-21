"""Settings, read from the environment (prefix ``MIXNET_NODE_``) or a ``.env`` file."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MIXNET_NODE_", env_file=".env", extra="ignore")

    node_name: str = "mixnet-node"
    key_dir: Path = Path("data/keys")
    """Where this node's per-election key pairs live. Keep it off the ballot box's hosts."""
    blob_dir: Path = Path("data/blobs")
    ballot_box_url: str = "http://localhost:8000"


@lru_cache
def get_settings() -> Settings:
    return Settings()
