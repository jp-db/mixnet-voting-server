"""ASGI application factory: ``uvicorn ballot_box.app:create_app --factory``."""

from __future__ import annotations

from fastapi import FastAPI

import evmlr

from .config import Settings, get_settings


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    app = FastAPI(title="Mixnet voting — ballot box", version="0.1.0")
    app.state.settings = settings

    @app.get("/health", tags=["ops"])
    def health() -> dict[str, object]:
        return {"status": "ok", "evmlr": {"modulus": evmlr.MODULUS, "vote_len": evmlr.VOTE_LEN}}

    # Routers to add: auth (login/logout/callbacks), elections (admin CRUD and
    # lifecycle), voters (roll upload, eligibility), booth (params, cast),
    # bulletin board (public ballots, trackers, transcript, results).
    return app
