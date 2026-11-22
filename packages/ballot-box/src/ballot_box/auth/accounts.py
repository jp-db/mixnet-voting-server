"""Accounts (who may manage what) and the principal behind an admin request."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Account, ElectionManager
from .base import Identity


def identity_key(provider: str, external_id: str) -> str:
    return f"{provider}:{external_id}"


def find_account(session: Session, identity: Identity) -> Account | None:
    return session.scalar(
        select(Account).where(
            Account.provider == identity.provider, Account.external_id == identity.external_id
        )
    )


def record_login(session: Session, identity: Identity, admin_identities: list[str]) -> Account:
    """Creates or refreshes the identity's account. Identities in ``admin_identities`` become admins."""
    account = find_account(session, identity)
    if account is None:
        account = Account(provider=identity.provider, external_id=identity.external_id)
        session.add(account)
    account.email = identity.email or account.email
    account.display_name = identity.display_name or account.display_name
    account.last_login_at = datetime.now(UTC)
    if identity_key(identity.provider, identity.external_id) in admin_identities:
        account.is_admin = True
    session.flush()
    return account


@dataclass(frozen=True)
class Principal:
    """Who is calling the admin API: an account, or the admin token (``account`` None)."""

    account: Account | None
    is_admin: bool
    can_manage: bool

    def manages(self, session: Session, election_id: object) -> bool:
        if self.is_admin:
            return True
        if self.account is None:
            return False
        return session.get(ElectionManager, (election_id, self.account.id)) is not None
