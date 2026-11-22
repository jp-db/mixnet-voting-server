"""Who is logged in: the identity kept in the signed session cookie.

A login route (one per :class:`~ballot_box.auth.base.AuthProvider`) calls
:func:`log_in` once the provider has authenticated the user; routes ask for
:func:`current_identity` or :data:`RequiredIdentity`.

The cookie is signed with ``BALLOT_BOX_SECRET_KEY`` (Starlette's
``SessionMiddleware``), ``SameSite=Lax``, and ``Secure`` when the public URL
is HTTPS. State-changing routes also check the ``Origin`` header
(:func:`same_origin`), so another site cannot make a logged-in browser cast.
"""

from __future__ import annotations

from typing import Annotated, Any
from urllib.parse import urlsplit

from fastapi import Depends, HTTPException, Request, status

from .base import Identity

_KEY = "identity"


def log_in(request: Request, identity: Identity) -> None:
    request.session.clear()  # a fresh session for the new identity
    request.session[_KEY] = {
        "provider": identity.provider,
        "external_id": identity.external_id,
        "email": identity.email,
        "display_name": identity.display_name,
    }


def log_out(request: Request) -> None:
    request.session.clear()


def current_identity(request: Request) -> Identity | None:
    data: dict[str, Any] | None = request.session.get(_KEY)
    if not data:
        return None
    return Identity(
        provider=data["provider"],
        external_id=data["external_id"],
        email=data.get("email"),
        display_name=data.get("display_name"),
    )


def require_identity(identity: Annotated[Identity | None, Depends(current_identity)]) -> Identity:
    if identity is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "log in first")
    return identity


RequiredIdentity = Annotated[Identity, Depends(require_identity)]


def _origin(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}"


def same_origin(request: Request) -> None:
    """Refuses a cross-site request. Browsers send ``Origin`` with every POST."""
    origin = request.headers.get("origin")
    if origin is not None and origin != _origin(request.app.state.settings.public_url):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "cross-site request refused")
