"""The ``cas`` provider: log in at a CAS server (protocol 3.0), validate the ticket on return.

The service URL sent to CAS is this server's callback with the page to
return to; CAS requires the same service URL when the ticket is validated,
so both are built here from ``public_url``.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlencode

import cas

from .base import Identity

NAME = "cas"


class CASProvider:
    def __init__(self, server_url: str, public_url: str, http_session: Any = None) -> None:
        """``http_session`` is a ``requests.Session`` (tests pass a stand-in)."""
        self.server_url = server_url.rstrip("/") + "/"
        self.public_url = public_url.rstrip("/")
        self.http_session = http_session

    def _client(self, next_path: str) -> cas.CASClientV3:
        service = f"{self.public_url}/auth/cas/callback?{urlencode({'next': next_path})}"
        return cas.CASClient(
            version=3, service_url=service, server_url=self.server_url, session=self.http_session
        )

    def login_url(self, next_path: str) -> str:
        return self._client(next_path).get_login_url()

    def validate(self, ticket: str, next_path: str) -> Identity | None:
        """Asks the CAS server about the ticket. Blocking: call from a thread."""
        user, attributes, _ = self._client(next_path).verify_ticket(ticket)
        if not user:
            return None
        attributes = attributes or {}
        email = attributes.get("mail") or attributes.get("email")
        name = attributes.get("displayName") or attributes.get("cn")
        return Identity(
            provider=NAME,
            external_id=user,
            email=email if isinstance(email, str) else None,
            display_name=name if isinstance(name, str) else None,
            attributes=attributes,
        )
