"""Migrations, sealing, the node client."""

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext

from ballot_box.db import make_engine, migrate
from ballot_box.models import Base
from ballot_box.sealing import UnsealError, node_token_sealer


def test_migrations_match_the_models(database_url):
    url = database_url
    migrate(url)
    with make_engine(url).connect() as conn:
        assert compare_metadata(MigrationContext.configure(conn), Base.metadata) == []


def test_sealing():
    sealer = node_token_sealer("k" * 40)
    sealed = sealer.seal(b"secret", b"node-0")
    assert sealer.unseal(sealed, b"node-0") == b"secret"
    with pytest.raises(UnsealError):
        sealer.unseal(sealed, b"node-1")  # moved to another node
    with pytest.raises(UnsealError):
        node_token_sealer("other" * 8).unseal(sealed, b"node-0")


def test_keygen_through_celery(monkeypatch, settings, services, queue):
    """The Celery task and queue wiring, run eagerly in process."""
    from fastapi.testclient import TestClient

    from ballot_box.config import get_settings

    # ballot_box.worker reads the settings when imported (for the broker URL).
    monkeypatch.setenv("BALLOT_BOX_SECRET_KEY", "test")
    get_settings.cache_clear()
    from ballot_box import worker
    from ballot_box.app import create_app
    from ballot_box.tasks import CeleryQueue

    from .conftest import ADMIN_TOKEN

    monkeypatch.setattr(worker, "_services", services)
    monkeypatch.setitem(worker.app.conf, "task_always_eager", True)
    app = create_app(settings, services=services, queue=CeleryQueue())
    with TestClient(app, headers={"Authorization": f"Bearer {ADMIN_TOKEN}"}) as api:
        q = {"title": "Q", "answers": ["a", "b"]}
        e = api.post("/api/admin/elections", json={"title": "E", "questions": [q]}).json()
        assert api.post(f"/api/admin/elections/{e['id']}/freeze").json()["state"] == "keygen"
        assert api.get(f"/api/admin/elections/{e['id']}").json()["state"] == "frozen"
