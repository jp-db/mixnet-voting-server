"""The registry of mixnet nodes, and connecting to them."""

from __future__ import annotations

from typing import Protocol

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import Settings
from ..models import MixnetNode
from ..sealing import Sealer
from .client import MixnetClient


def register_node(session: Session, sealer: Sealer, name: str, url: str, token: str) -> MixnetNode:
    """Adds a node, or updates the URL and token of the node with that name."""
    node = session.scalar(select(MixnetNode).where(MixnetNode.name == name))
    if node is None:
        node = MixnetNode(name=name, url=url, token_sealed=b"")
        session.add(node)
    node.url = url.rstrip("/")
    node.token_sealed = sealer.seal(token.encode(), name.encode())
    return node


def sync_default_nodes(session: Session, settings: Settings, sealer: Sealer) -> None:
    """Copies ``settings.mixnet_nodes`` into the registry, as the default nodes in that order.

    Nodes no longer in the settings stay registered (elections may use them)
    but stop being defaults.
    """
    names = [n.name for n in settings.mixnet_nodes]
    for node in session.scalars(select(MixnetNode).where(MixnetNode.is_default)):
        if node.name not in names:
            node.is_default, node.default_position = False, None
    for position, cfg in enumerate(settings.mixnet_nodes):
        node = register_node(session, sealer, cfg.name, cfg.url, cfg.token.get_secret_value())
        node.is_default, node.default_position = True, position


def default_nodes(session: Session) -> list[MixnetNode]:
    return list(
        session.scalars(select(MixnetNode).where(MixnetNode.is_default).order_by(MixnetNode.default_position))
    )


class Connector(Protocol):
    def __call__(self, node: MixnetNode) -> MixnetClient: ...


class HttpConnector:
    """Opens (and reuses) one HTTP client per node."""

    def __init__(self, sealer: Sealer, timeout: float = 120.0) -> None:
        self.sealer = sealer
        self.timeout = timeout
        self._clients: dict[tuple[str, bytes], MixnetClient] = {}

    def __call__(self, node: MixnetNode) -> MixnetClient:
        cache_key = (node.url, node.token_sealed)
        client = self._clients.get(cache_key)
        if client is None:
            token = self.sealer.unseal(node.token_sealed, node.name.encode()).decode()
            http = httpx.Client(
                base_url=node.url, headers={"Authorization": f"Bearer {token}"}, timeout=self.timeout
            )
            client = self._clients[cache_key] = MixnetClient(http, node.name)
        return client

    def close(self) -> None:
        for client in self._clients.values():
            client.http.close()
        self._clients.clear()
