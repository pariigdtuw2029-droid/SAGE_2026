"""
Database + data-processing layer (Member 2).

Public surface:
    connection      — engine, SessionLocal, Base, init_db(), get_db()
    models          — six ORM tables: lots, components, measurements,
                      predictions, risk_assessments, explanations
    validation      — pre-storage validation (columns, IDs, numbers, temps)
    preprocessing   — cleaning, units, imputation, timestamps, relationships
    ml_inference    — runs the 3 SAGE model artifacts over clean data
    crud            — insert/update/delete + reusable queries + report prep
    ingestion       — end-to-end CSV → database pipeline

CLI (from backend/):  python -m database init | ingest <csv> | drop | stats
"""

from database import (
    connection,
    crud,
    ingestion,
    ml_inference,
    models,
    preprocessing,
    validation,
)
from database.connection import (
    Base,
    SessionLocal,
    drop_db,
    engine,
    get_db,
    get_database_url,
    init_db,
)
from database.crud import session_scope

__all__ = [
    "Base", "SessionLocal", "engine", "get_db", "init_db", "drop_db",
    "get_database_url", "session_scope",
    "connection", "models", "validation", "preprocessing",
    "ml_inference", "crud", "ingestion",
]