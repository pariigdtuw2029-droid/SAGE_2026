"""
Database connection management (Member 2).

Single source of truth for the SQLAlchemy engine / SessionLocal / Base.
The connection URL comes from DATABASE_URL (see .env.example); it defaults
to a SQLite file so the whole pipeline runs with zero external services —
swap in PostgreSQL by setting the env var, no code changes needed:

    DATABASE_URL=postgresql+psycopg://user:password@localhost:5432/astra_guard
"""

import os
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

# Load backend/.env if present (same convention as app/config usage)
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

BACKEND_DIR = Path(__file__).resolve().parent.parent
DEFAULT_SQLITE_URL = f"sqlite:///{BACKEND_DIR / 'astra_guard.db'}"


def get_database_url() -> str:
    url = os.getenv("DATABASE_URL", DEFAULT_SQLITE_URL).strip()
    if not url:
        url = DEFAULT_SQLITE_URL
    # Accept the plain postgresql:// scheme too (common in env samples);
    # psycopg3 driver is the preferred modern choice.
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+psycopg://", 1)
    return url


DATABASE_URL = get_database_url()

# check_same_thread is SQLite-only and harmless to omit elsewhere; pass_* kwargs
# would break other dialects, so scope them.
_connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}

engine = create_engine(DATABASE_URL, connect_args=_connect_args, pool_pre_ping=True)

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""


def init_db() -> None:
    """Create all tables. Idempotent (checkfirst=True by default)."""
    # Import so the mappers are registered on Base before create_all.
    from database import models  # noqa: F401

    Base.metadata.create_all(bind=engine)


def drop_db() -> None:
    """Drop all tables (used by tests and re-ingestion)."""
    from database import models  # noqa: F401

    Base.metadata.drop_all(bind=engine)


def get_db():
    """FastAPI-style dependency yielding a session; always closes."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
