"""Settings, read from the environment (prefix ``BALLOT_BOX_``) or a ``.env`` file."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class DefaultMixnetNode(BaseModel):
    """A mixnet node the ballot box offers by default for new elections."""

    name: str = Field(min_length=1, max_length=100)
    url: str
    """Base URL of the node's API, e.g. ``http://mixnet-node-0:8100``."""
    token: SecretStr
    """The node's ``MIXNET_NODE_API_TOKEN``."""


class CasServer(BaseModel):
    """A CAS service people can log in with. Its ``name`` is the provider of the
    identities it vouches for (``ufsc:jdoe``), so keep it stable."""

    name: str = Field(pattern=r"^[a-z][a-z0-9-]{0,49}$")
    label: str = Field(min_length=1, max_length=100)
    """What the login page and the election pages show, e.g. "UFSC (idUFSC)"."""
    url: str = Field(pattern=r"^https?://")
    """Base URL of the CAS server, e.g. ``https://cas.example.edu/cas/``."""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="BALLOT_BOX_", env_file=".env", extra="ignore")

    secret_key: SecretStr
    """Signs session cookies and CSRF tokens, and seals the mixnet node tokens
    stored in the database. Required; no default on purpose. Changing it makes
    the stored node tokens unreadable: re-enter them."""

    database_url: str = "postgresql+psycopg://voting:voting@localhost:5432/voting"
    redis_url: str = "redis://localhost:6379/0"
    blob_dir: Path = Path("data/blobs")

    public_url: str = "http://localhost:8000"
    """Base URL voters reach; used for CAS/OIDC callback URLs."""

    auth_providers: list[str] = ["password"]
    """Enabled login methods, in display order: ``password`` and the names of CAS services."""
    cas_servers: list[CasServer] = []
    """The CAS services, as JSON: ``[{"name": "ufsc", "label": "UFSC", "url": "https://..."}]``."""
    cas_server_url: str | None = None
    """Shorthand for one CAS service named ``cas``."""

    admin_identities: list[str] = []
    """Identities made administrators when they log in, as ``provider:external_id``
    (e.g. ``["cas:jdoe", "password:admin"]``): how the first administrator gets in."""

    admin_token: SecretStr | None = Field(default=None, min_length=32)
    """Optional bearer token for the ``/api/admin`` routes, with administrator
    rights, for scripts and automation. People log in instead."""

    mixnet_nodes: list[DefaultMixnetNode] = []
    """The default mixnet nodes, in their default mixing order, as JSON:
    ``[{"name": "mixnet-node-0", "url": "http://...", "token": "..."}, ...]``.
    They are copied into the node registry at startup; an election manager can
    pick other registered nodes, or another order."""

    max_ballot_bytes: int = Field(default=4 * 1024 * 1024, gt=0)
    """Largest ballot accepted. A ballot is about 34 KB per mix server."""

    mix_poll_seconds: float = Field(default=2.0, gt=0)
    """How often the counting task polls a mixnet node for its job."""

    schedule_seconds: float = Field(default=15.0, gt=0)
    """How often the scheduler (``celery beat``) opens and closes elections whose time has come."""

    @property
    def cas_services(self) -> dict[str, CasServer]:
        """Every CAS service by name, ``cas_server_url`` included."""
        out = {c.name: c for c in self.cas_servers}
        if self.cas_server_url:
            out.setdefault(
                "cas", CasServer(name="cas", label="Single sign-on (CAS)", url=self.cas_server_url)
            )
        return out

    def provider_label(self, name: str) -> str:
        if name == "password":
            return "Username and password"
        cas = self.cas_services.get(name)
        return cas.label if cas else name

    @model_validator(mode="after")
    def _check_providers(self) -> Settings:
        names = [c.name for c in self.cas_servers]
        if len(set(names)) != len(names) or "password" in names:
            raise ValueError("CAS service names must be distinct, and not 'password'")
        unknown = set(self.auth_providers) - {"password", *self.cas_services}
        if unknown:
            names = ", ".join(sorted(unknown))
            raise ValueError(
                f"unknown auth providers: {names} (a CAS one needs cas_servers or cas_server_url)"
            )
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]  # filled from the environment
