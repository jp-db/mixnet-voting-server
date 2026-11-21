"""ASGI application factory: ``uvicorn mixnet_node.app:create_app --factory``."""

from __future__ import annotations

from fastapi import FastAPI

from .config import Settings, get_settings


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    app = FastAPI(title=f"Mixnet voting — {settings.node_name}", version="0.1.0")
    app.state.settings = settings

    @app.get("/health", tags=["ops"])
    def health() -> dict[str, str]:
        return {"status": "ok", "node": settings.node_name}

    # Routers to add: keygen and mix (see the package docstring). Mixing is
    # CPU-bound: run it off the event loop, in a process pool or a worker.
    return app
