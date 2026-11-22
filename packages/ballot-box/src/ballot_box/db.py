"""Engine, sessions and migrations."""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

_MIGRATIONS = Path(__file__).parent / "migrations"


def make_engine(url: str) -> Engine:
    if url.startswith("sqlite"):
        # SQLite is for tests and local experiments; the deployment uses Postgres.
        engine = create_engine(url, connect_args={"check_same_thread": False})

        @event.listens_for(engine, "connect")
        def _foreign_keys(dbapi_conn, _record) -> None:  # type: ignore[no-untyped-def]
            dbapi_conn.execute("PRAGMA foreign_keys=ON")

        return engine
    return create_engine(url, pool_pre_ping=True)


def make_sessionmaker(engine: Engine) -> sessionmaker[Session]:
    # Objects stay usable after commit: tasks read node URLs after their transaction.
    return sessionmaker(engine, expire_on_commit=False)


def alembic_config(url: str) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(_MIGRATIONS))
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg


def migrate(url: str, revision: str = "head") -> None:
    """Brings the database at ``url`` up to ``revision``."""
    command.upgrade(alembic_config(url), revision)


if __name__ == "__main__":  # python -m ballot_box.db: migrate the configured database
    from .config import get_settings

    migrate(get_settings().database_url)
