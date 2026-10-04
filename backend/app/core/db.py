from datetime import datetime, timezone
from typing import Generator

import logging
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session, declarative_base, sessionmaker

from .config import settings

def _build_engine():
    """Engine with a pool sized for high-concurrency traffic (Postgres).
    SQLite (test/dev fallback) uses defaults — it does not pool the same way."""
    if settings.DATABASE_URL.startswith("sqlite"):
        return create_engine(settings.DATABASE_URL, pool_pre_ping=True)
    return create_engine(
        settings.DATABASE_URL,
        pool_size=settings.DB_POOL_SIZE,
        max_overflow=settings.DB_MAX_OVERFLOW,
        pool_recycle=settings.DB_POOL_RECYCLE,
        pool_pre_ping=True,
    )


engine = _build_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base = declarative_base()


def ensure_schema() -> None:
    """Heal databases that were created by an *earlier* build of the app.

    ``Base.metadata.create_all`` only creates **missing tables** — it never
    adds new **columns** to tables that already exist. A database created by
    an older version of the app therefore breaks with
    ``psycopg2.errors.UndefinedColumn`` on the first query that touches a
    column added after that database was created.

    This compares every model table with the live database and issues
    ``ALTER TABLE ... ADD COLUMN`` for anything missing. It is a no-op once
    the schema is up to date, so it is safe to run on every startup.
    """
    added: list[str] = []
    inspector = inspect(engine)
    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            existing = {c["name"] for c in inspector.get_columns(table.name)}
            if not existing:
                continue  # table just created by create_all — already complete
            for col in table.columns:
                if col.name in existing:
                    continue
                coltype = col.type.compile(engine.dialect)
                conn.execute(
                    text(f"ALTER TABLE {table.name} ADD COLUMN {col.name} {coltype}")
                )
                added.append(f"{table.name}.{col.name}")
    if added:
        logging.getLogger(__name__).warning(
            "auto-migration: added missing columns %s (database was created "
            "by an older build — it is now up to date)", ", ".join(added)
        )


def utcnow() -> datetime:
    """Naive UTC timestamp used consistently across the system."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
