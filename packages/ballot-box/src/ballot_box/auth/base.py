"""The interface every login method implements.

Authentication (who is this person?) is kept apart from eligibility (may
they vote in this election?). A provider only returns an :class:`Identity`;
the voter roll of each election decides what that identity may do, by
matching on ``(provider, external_id)``, on the email, or on provider
attributes (e.g. a CAS ``memberOf``).

Planned implementations: ``PasswordProvider`` (argon2 hashes in the users
table), ``CASProvider`` (python-cas: redirect to the CAS login, validate the
service ticket on callback) and ``OIDCProvider`` (Authlib).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

from fastapi import Request, Response


@dataclass(frozen=True)
class Identity:
    provider: str
    """Name of the provider that authenticated the user (``password``, ``cas``, ...)."""
    external_id: str
    """Stable identifier within the provider (username, CAS user, OIDC ``sub``)."""
    email: str | None = None
    display_name: str | None = None
    attributes: Mapping[str, Any] = field(default_factory=dict)


class AuthProvider(Protocol):
    name: str
    """Short, stable identifier; stored with every user record."""
    label: str
    """What the login page shows."""

    async def begin(self, request: Request, return_to: str) -> Response:
        """Starts a login: renders a form, or redirects to the external server."""
        ...

    async def complete(self, request: Request) -> Identity:
        """Finishes a login (form post or callback). Raises on failure."""
        ...
