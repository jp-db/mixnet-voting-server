"""Login and logout, under ``/auth``.

    ``GET  /auth/providers``        the enabled login methods, in display order
    ``POST /auth/password/login``   JSON ``{username, password}``
    ``GET  /auth/cas/login``        redirects to the CAS server (``?next=/path`` to come back to)
    ``GET  /auth/cas/callback``     where CAS sends the browser back with a ticket
    ``GET  /auth/me``               the logged-in identity and its account's roles
    ``POST /auth/logout``

A successful login replaces the session (no fixation) and records the
account (:func:`ballot_box.auth.accounts.record_login`).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from ..auth import RequiredIdentity, log_in, log_out, same_origin
from ..auth import password as password_auth
from ..auth.accounts import find_account, record_login
from ..auth.base import Identity
from ..auth.cas import CASProvider
from ..services import Services
from .deps import Db

router = APIRouter(prefix="/auth", tags=["auth"])

_LABELS = {"password": "Username and password", "cas": "Single sign-on (CAS)"}


def safe_next(path: str | None) -> str:
    """Only paths on this site: no open redirect through ``next``."""
    if not path or not path.startswith("/") or path.startswith("//") or "\\" in path:
        return "/"
    return path


def _enabled(request: Request, name: str) -> None:
    if name not in request.app.state.settings.auth_providers:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"the {name} login is not enabled")


def _finish_login(request: Request, svc: Services, identity: Identity) -> None:
    with svc.db.begin() as db:
        record_login(db, identity, request.app.state.settings.admin_identities)
    log_in(request, identity)


class ProviderOut(BaseModel):
    name: str
    label: str
    login_url: str


@router.get("/providers", response_model=list[ProviderOut])
def providers(request: Request) -> list[ProviderOut]:
    urls = {"password": "/auth/password/login", "cas": "/auth/cas/login"}
    return [
        ProviderOut(name=n, label=_LABELS[n], login_url=urls[n])
        for n in request.app.state.settings.auth_providers
    ]


class PasswordLogin(BaseModel):
    username: str = Field(min_length=1, max_length=150)
    password: str = Field(min_length=1, max_length=1000)


@router.post("/password/login", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(same_origin)])
def password_login(body: PasswordLogin, request: Request) -> None:
    _enabled(request, password_auth.NAME)
    svc: Services = request.app.state.services
    with svc.db.begin() as db:
        identity = password_auth.authenticate(db, body.username, body.password)
    if identity is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "wrong username or password")
    _finish_login(request, svc, identity)


def _cas(request: Request) -> CASProvider:
    _enabled(request, "cas")
    return request.app.state.cas


@router.get("/cas/login")
def cas_login(request: Request, next: Annotated[str | None, Query()] = None) -> RedirectResponse:
    return RedirectResponse(_cas(request).login_url(safe_next(next)), status.HTTP_303_SEE_OTHER)


@router.get("/cas/callback")
async def cas_callback(
    request: Request, ticket: Annotated[str, Query(min_length=1, max_length=2000)], next: str | None = None
) -> RedirectResponse:
    provider = _cas(request)
    path = safe_next(next)
    identity = await run_in_threadpool(provider.validate, ticket, path)
    if identity is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "the CAS server did not accept the ticket")
    await run_in_threadpool(_finish_login, request, request.app.state.services, identity)
    return RedirectResponse(path, status.HTTP_303_SEE_OTHER)


class MeOut(BaseModel):
    provider: str
    external_id: str
    email: str | None
    display_name: str | None
    is_admin: bool
    can_manage: bool
    """May use the management pages: an administrator, a manager, or a manager of some election."""
    can_create: bool
    """May create elections."""


@router.get("/me", response_model=MeOut)
def me(identity: RequiredIdentity, db: Db) -> MeOut:
    from .admin import manages_any

    account = find_account(db, identity)
    can_create = bool(account and (account.can_manage or account.is_admin))
    return MeOut(
        provider=identity.provider,
        external_id=identity.external_id,
        email=identity.email,
        display_name=identity.display_name,
        is_admin=bool(account and account.is_admin),
        can_manage=can_create or bool(account and manages_any(db, account)),
        can_create=can_create,
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(same_origin)])
def logout(request: Request) -> None:
    log_out(request)
