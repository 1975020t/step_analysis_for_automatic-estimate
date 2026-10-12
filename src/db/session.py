"""Database connection: DATABASE_URL (PostgreSQL in docker-compose, e.g. postgresql+psycopg://user:pass@db/estimate);
without it, SQLite in the storage folder (<ESTIMATE_STORAGE_DIR>/app.db), which is what the tests use.

`open_database(url)` applies the migrations (Alembic, migrations/) and returns a session factory.
"""
from __future__ import annotations

import os
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

ROOT = Path(__file__).resolve().parents[2]


def database_url(storage_dir: str | Path | None = None) -> str:
    url = os.getenv("DATABASE_URL", "").strip()
    if url:
        return url
    storage = Path(storage_dir or os.getenv("ESTIMATE_STORAGE_DIR") or "output")
    storage.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{storage.resolve() / 'app.db'}"


def make_engine(url: str) -> Engine:
    if url.startswith("sqlite"):
        engine = create_engine(url, connect_args={"check_same_thread": False, "timeout": 30})

        @event.listens_for(engine, "connect")
        def _pragmas(dbapi_connection, _):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.close()

        return engine
    return create_engine(url, pool_pre_ping=True)


def migrate(engine: Engine) -> None:
    """Bring the schema to the latest migration (alembic upgrade head)."""
    from alembic import command
    from alembic.config import Config

    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "migrations"))
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "head")


def open_database(url: str) -> sessionmaker[Session]:
    engine = make_engine(url)
    migrate(engine)
    return sessionmaker(engine, expire_on_commit=False)
