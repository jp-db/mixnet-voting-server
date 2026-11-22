"""Pluggable authentication. See :mod:`ballot_box.auth.base` and :mod:`ballot_box.auth.session`."""

from .base import AuthProvider, Identity
from .session import RequiredIdentity, current_identity, log_in, log_out, same_origin

__all__ = [
    "AuthProvider",
    "Identity",
    "RequiredIdentity",
    "current_identity",
    "log_in",
    "log_out",
    "same_origin",
]
