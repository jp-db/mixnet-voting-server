"""Settings, read from the environment (prefix ``MIXNET_NODE_``) or a ``.env`` file."""

from __future__ import annotations

import base64
import binascii
from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MIXNET_NODE_", env_file=".env", extra="ignore")

    node_name: str = "mixnet-node"

    api_token: SecretStr = Field(min_length=32)
    """Bearer token the ballot box must present. Required. Terminate mTLS in
    front of the node too when it runs at another organisation."""

    key_encryption_key: SecretStr
    """32 bytes, base64: seals the election key pairs on disk. Required. Losing
    it makes every key, and so every open election, unrecoverable."""

    data_dir: Path = Path("data")
    """Holds keys/, jobs/ and blobs/. Keep it off the ballot box's hosts."""

    mix_workers: int = Field(default=1, ge=1)
    """Processes that run mix jobs. evmlr calls are serialised per process."""

    @field_validator("key_encryption_key")
    @classmethod
    def _kek_is_32_bytes(cls, v: SecretStr) -> SecretStr:
        try:
            raw = base64.b64decode(v.get_secret_value(), validate=True)
        except binascii.Error as e:
            raise ValueError("key_encryption_key must be base64") from e
        if len(raw) != 32:
            raise ValueError("key_encryption_key must decode to 32 bytes")
        return v

    @property
    def kek_bytes(self) -> bytes:
        return base64.b64decode(self.key_encryption_key.get_secret_value())

    @property
    def key_dir(self) -> Path:
        return self.data_dir / "keys"

    @property
    def job_dir(self) -> Path:
        return self.data_dir / "jobs"

    @property
    def blob_dir(self) -> Path:
        return self.data_dir / "blobs"


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]  # filled from the environment
