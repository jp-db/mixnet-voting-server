"""The ``password`` provider: usernames and argon2id hashes in the database.

Accounts are created by an administrator (admin API, or
``python -m ballot_box.accounts set-password``); there is no self sign-up.
A wrong username takes as long as a wrong password, so the login form does
not tell which usernames exist.
"""

from __future__ import annotations

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from sqlalchemy.orm import Session

from ..models import PasswordCredential
from .base import Identity

NAME = "password"
MIN_LENGTH = 10

_hasher = PasswordHasher()  # argon2id with the library's current recommended parameters
_DUMMY_HASH = _hasher.hash("not a password: equalises the timing of unknown usernames")


class WeakPassword(ValueError):
    pass


def set_password(
    session: Session,
    username: str,
    password: str,
    *,
    email: str | None = None,
    display_name: str | None = None,
) -> PasswordCredential:
    """Creates the login, or replaces its password (and email and name, when given)."""
    if len(password) < MIN_LENGTH:
        raise WeakPassword(f"passwords have at least {MIN_LENGTH} characters")
    cred = session.get(PasswordCredential, username)
    if cred is None:
        cred = PasswordCredential(username=username, password_hash="")
        session.add(cred)
    cred.password_hash = _hasher.hash(password)
    if email is not None:
        cred.email = email
    if display_name is not None:
        cred.display_name = display_name
    return cred


def authenticate(session: Session, username: str, password: str) -> Identity | None:
    """The identity if the password is right, else None. Upgrades outdated hashes on success."""
    cred = session.get(PasswordCredential, username)
    try:
        _hasher.verify(cred.password_hash if cred else _DUMMY_HASH, password)
    except (VerificationError, InvalidHashError):
        return None
    if cred is None:
        return None
    if _hasher.check_needs_rehash(cred.password_hash):
        cred.password_hash = _hasher.hash(password)
    return Identity(
        provider=NAME, external_id=cred.username, email=cred.email, display_name=cred.display_name
    )
