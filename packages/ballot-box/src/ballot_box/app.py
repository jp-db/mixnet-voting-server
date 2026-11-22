"""ASGI application factory: ``uvicorn ballot_box.app:create_app --factory``.

The database must be migrated first (``python -m ballot_box.db``).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

import evmlr

from .api import admin
from .config import Settings, get_settings
from .mixnet import sync_default_nodes
from .services import Services
from .tasks import CeleryQueue, TaskQueue


def create_app(
    settings: Settings | None = None, services: Services | None = None, queue: TaskQueue | None = None
) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        svc = app.state.services = services or Services.from_settings(settings)
        with svc.db.begin() as session:
            sync_default_nodes(session, settings, svc.sealer)
        yield

    app = FastAPI(title="Mixnet voting — ballot box", version="0.1.0", lifespan=lifespan)
    app.state.settings = settings
    app.state.queue = queue or CeleryQueue()

    @app.get("/health", tags=["ops"])
    def health() -> dict[str, object]:
        return {"status": "ok", "evmlr": {"modulus": evmlr.MODULUS, "vote_len": evmlr.VOTE_LEN}}

    app.include_router(admin.router)
    # Still to add: auth (login/logout/callbacks), the voter-facing booth
    # (parameters, casting) and the public bulletin board.
    return app
