"""Pluggable authentication. See :mod:`ballot_box.auth.base`."""

from .base import AuthProvider, Identity

__all__ = ["AuthProvider", "Identity"]
