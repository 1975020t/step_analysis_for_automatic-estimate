"""Database connection: DATABASE_URL (PostgreSQL in docker-compose, e.g. postgresql+psycopg://user:pass@db/estimate;
Aurora DSQL on AWS, e.g. auroradsql+psycopg://admin@<cluster endpoint>/postgres?region=ap-northeast-1);
without it, SQLite in the storage folder (<ESTIMATE_STORAGE_DIR>/app.db), which is what the tests use.

`open_database(url)` applies the migrations (Alembic, migrations/) and returns a session factory.

Aurora DSQL (dialect `auroradsql`, package aurora-dsql-sqlalchemy): the password is an IAM token that the connector
makes for each new connection from the AWS credentials of the process (the role of the Lambda); the query part of
the URL (region, profile, ...) goes to the connector. DSQL runs one DDL statement per transaction and never DDL
beside data changes, so an empty DSQL database gets the schema of the models in one go (`create_all`, the dialect
commits each statement) and is marked as at the latest migration; a later migration must keep to what DSQL allows.
A conflicting write fails at commit with SQLSTATE 40001 (src/db/retry.py runs the work again).
"""
from __future__ import annotations

import os
from pathlib import Path

from alembic.ddl.postgresql import PostgresqlImpl
from sqlalchemy import create_engine, event, inspect
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import Session, sessionmaker

ROOT = Path(__file__).resolve().parents[2]
DSQL_POOL_RECYCLE_S = 3000  # DSQL closes a connection after an hour


class AuroraDsqlImpl(PostgresqlImpl):
    """Alembic's handling of the `auroradsql` dialect: PostgreSQL's, without DDL transactions."""
    __dialect__ = "auroradsql"
    transactional_ddl = False


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
    if url.startswith("auroradsql"):
        return dsql_engine(url)
    return create_engine(url, pool_pre_ping=True)


def dsql_engine(url: str) -> Engine:
    from aurora_dsql_sqlalchemy import create_dsql_engine

    parsed = make_url(url)
    return create_dsql_engine(host=parsed.host, user=parsed.username or "admin",
                              driver=parsed.drivername.partition("+")[2] or "psycopg",
                              dbname=parsed.database or "postgres", connect_args=dict(parsed.query) or None,
                              pool_pre_ping=True, pool_recycle=DSQL_POOL_RECYCLE_S)


def migrate(engine: Engine, from_models: bool | None = None) -> None:
    """Bring the schema to the latest migration (alembic upgrade head).

    from_models (default: on Aurora DSQL): an empty database gets the tables of the models (create_all) and is
    stamped as at the latest migration instead of running every migration."""
    from alembic import command
    from alembic.config import Config

    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "migrations"))
    if from_models is None:
        from_models = engine.dialect.name == "auroradsql"
    if from_models and not inspect(engine).has_table("alembic_version"):
        from src.db.models import Base

        Base.metadata.create_all(engine)
        with engine.begin() as connection:
            config.attributes["connection"] = connection
            command.stamp(config, "head")
        return
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "head")


def open_database(url: str) -> sessionmaker[Session]:
    import src.db.tenant_filter  # noqa: F401 - every session sees only the current company's rows

    engine = make_engine(url)
    migrate(engine)
    return sessionmaker(engine, expire_on_commit=False)
