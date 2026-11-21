"""ASGI application factory: ``uvicorn mixnet_node.app:create_app --factory``.

Run a single server process (no ``--workers``): the per-election mix lock
lives in that process. Mixing runs in its own process pool.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from .api import router
from .config import Settings, get_settings
from .jobs import MixService


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.mix_service = MixService(settings)
        try:
            yield
        finally:
            app.state.mix_service.shutdown()

    app = FastAPI(title=f"Mixnet voting — {settings.node_name}", version="0.1.0", lifespan=lifespan)
    app.state.settings = settings

    @app.get("/health", tags=["ops"])
    def health() -> dict[str, str]:
        return {"status": "ok", "node": settings.node_name}

    app.include_router(router)
    return app
