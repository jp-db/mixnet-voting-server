"""A ballot box wired to three real mixnet nodes, in process.

The nodes are the mixnet-node FastAPI app behind ``TestClient``; the ballot
box reaches them through a connector that unseals each node's stored token,
as the HTTP connector does. Celery is replaced by :class:`SyncQueue`, which
runs the same task bodies in the test's thread when ``drain()`` is called.
"""

from __future__ import annotations

import base64
import os
import time
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import Request
from fastapi.testclient import TestClient
from sqlalchemy import text

from ballot_box.app import create_app
from ballot_box.auth import Identity, log_in, log_out
from ballot_box.config import Settings
from ballot_box.db import make_engine, make_sessionmaker, migrate
from ballot_box.elections.counting import Step, advance_count
from ballot_box.elections.keygen import run_keygen
from ballot_box.mixnet import MixnetClient, MixnetUnavailable
from ballot_box.models import Base, MixnetNode
from ballot_box.sealing import node_token_sealer
from ballot_box.services import Services
from mixnet_node.app import create_app as create_node_app
from mixnet_node.config import Settings as NodeSettings
from voting_common.blobstore import FilesystemBlobStore

ADMIN_TOKEN = "a" * 40
SECRET_KEY = "s" * 40
NODE_NAMES = ["node-0", "node-1", "node-2"]


def node_token(name: str) -> str:
    return f"token-of-{name}-".ljust(40, "x")


@pytest.fixture(scope="session")
def node_apps(tmp_path_factory: pytest.TempPathFactory) -> Iterator[dict[str, TestClient]]:
    """URL -> the node's client, lifespan running. Four nodes: three defaults and a spare."""
    clients = {}
    for name in [*NODE_NAMES, "spare"]:
        settings = NodeSettings(
            node_name=name,
            api_token=node_token(name),
            key_encryption_key=base64.b64encode(os.urandom(32)).decode(),
            data_dir=tmp_path_factory.mktemp(name),
        )
        client = TestClient(create_node_app(settings))
        client.__enter__()
        clients[f"http://{name}"] = client
    yield clients
    for client in clients.values():
        client.__exit__(None, None, None)


class InProcessConnector:
    """Hands out the in-process node clients, authenticated with the unsealed stored token."""

    def __init__(self, sealer, node_apps: dict[str, TestClient]) -> None:
        self.sealer = sealer
        self.node_apps = node_apps
        self.down: set[str] = set()
        """Names of nodes that behave as unreachable."""

    def __call__(self, node: MixnetNode) -> MixnetClient:
        if node.name in self.down:
            raise MixnetUnavailable(f"{node.name} is down (test)")
        http = self.node_apps[node.url]
        token = self.sealer.unseal(node.token_sealed, node.name.encode()).decode()
        http.headers["Authorization"] = f"Bearer {token}"
        return MixnetClient(http, node.name)


class SyncQueue:
    def __init__(self, svc: Services) -> None:
        self.svc = svc
        self.keygens: list[uuid.UUID] = []
        self.counts: list[uuid.UUID] = []
        self.unavailable: list[str] = []

    def keygen(self, election_id: uuid.UUID) -> None:
        self.keygens.append(election_id)

    def count(self, question_id: uuid.UUID) -> None:
        self.counts.append(question_id)

    def drain(self, timeout: float = 60) -> None:
        """Runs every queued task to completion, as the workers would.

        A task that finds a node unreachable is dropped and noted in
        ``unavailable`` (a worker would retry it later).
        """
        keygens, self.keygens = self.keygens, []
        for eid in keygens:
            try:
                run_keygen(self.svc, eid)
            except MixnetUnavailable as e:
                self.unavailable.append(str(e))
        counts, self.counts = self.counts, []
        for qid in counts:
            deadline = time.monotonic() + timeout
            while True:
                try:
                    step = advance_count(self.svc, qid)
                except MixnetUnavailable as e:
                    self.unavailable.append(str(e))
                    break
                if step is Step.WAIT:
                    assert time.monotonic() < deadline, "a mix job never finished"
                    time.sleep(0.02)
                elif step is not Step.CONTINUE:
                    break


TEST_DATABASE_URL = os.environ.get("BALLOT_BOX_TEST_DATABASE_URL")
"""Run against this (Postgres) database instead of a fresh SQLite file; it is wiped after each test."""


@pytest.fixture
def database_url(tmp_path: Path) -> Iterator[str]:
    if TEST_DATABASE_URL is None:
        yield f"sqlite:///{tmp_path / 'ballot-box.db'}"
        return
    yield TEST_DATABASE_URL
    engine = make_engine(TEST_DATABASE_URL)
    with engine.begin() as conn:
        Base.metadata.drop_all(conn)
        conn.execute(text("DROP TABLE IF EXISTS alembic_version"))
    engine.dispose()


@pytest.fixture
def settings(tmp_path: Path, database_url: str) -> Settings:
    return Settings(
        secret_key=SECRET_KEY,
        database_url=database_url,
        blob_dir=tmp_path / "blobs",
        admin_token=ADMIN_TOKEN,
        auth_providers=["password", "cas", "ufsc"],
        cas_servers=[
            {"name": "cas", "label": "Example CAS", "url": "https://cas.example.edu/cas/"},
            {"name": "ufsc", "label": "UFSC", "url": "https://sso.example.br/cas/"},
        ],
        mixnet_nodes=[{"name": n, "url": f"http://{n}", "token": node_token(n)} for n in NODE_NAMES],
    )


@pytest.fixture
def services(settings: Settings, node_apps: dict[str, TestClient]) -> Services:
    migrate(settings.database_url)
    sealer = node_token_sealer(settings.secret_key.get_secret_value())
    return Services(
        settings=settings,
        db=make_sessionmaker(make_engine(settings.database_url)),
        blobs=FilesystemBlobStore(settings.blob_dir),
        sealer=sealer,
        connect=InProcessConnector(sealer, node_apps),
    )


@pytest.fixture
def queue(services: Services) -> SyncQueue:
    return SyncQueue(services)


@pytest.fixture
def api(settings: Settings, services: Services, queue: SyncQueue) -> Iterator[TestClient]:
    app = create_app(settings, services=services, queue=queue)
    with TestClient(app, headers={"Authorization": f"Bearer {ADMIN_TOKEN}"}) as client:
        yield client


PUBLIC = "http://localhost:8000"  # Settings.public_url's default


@pytest.fixture
def web(api: TestClient) -> TestClient:
    """A browser: no admin token, its own cookie jar."""
    app = api.app

    @app.post("/test/login/{external_id}")
    def _login(external_id: str, request: Request) -> None:
        log_in(request, Identity(provider="password", external_id=external_id))

    @app.post("/test/login-as/{provider}/{external_id}")
    def _login_as(provider: str, external_id: str, request: Request) -> None:
        log_in(request, Identity(provider=provider, external_id=external_id))

    @app.post("/test/logout")
    def _logout(request: Request) -> None:
        log_out(request)

    return TestClient(app, base_url=PUBLIC)
