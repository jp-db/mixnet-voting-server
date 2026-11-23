"""ASGI application factory: ``uvicorn ballot_box.app:create_app --factory``.

The database must be migrated first (``python -m ballot_box.db``).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from starlette.middleware.sessions import SessionMiddleware

import evmlr

from .api import admin, auth, voting
from .auth.cas import CASProvider
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
    app.state.cas = {
        name: CASProvider(name, c.url, settings.public_url) for name, c in settings.cas_services.items()
    }

    @app.get("/health", tags=["ops"])
    def health() -> dict[str, object]:
        return {"status": "ok", "evmlr": {"modulus": evmlr.MODULUS, "vote_len": evmlr.VOTE_LEN}}

    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.secret_key.get_secret_value(),
        session_cookie="ballot_box_session",
        max_age=12 * 3600,
        same_site="lax",
        https_only=settings.public_url.startswith("https://"),
    )
    app.include_router(auth.router)
    app.include_router(admin.router)
    app.include_router(voting.router)
    return app
