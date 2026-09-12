"""
Lot service layer.

Routers call these functions and never touch storage directly.
Database-first: queries Member 2's database layer via CRUD, falling back
to mock data when the database is empty or a record is not found.
"""

from typing import List, Optional

from app.services import mock_data
from database import crud
from database.connection import init_db

# Ensure database tables exist if running against a fresh database file.
try:
    init_db()
except Exception:
    pass


def list_lots() -> List[dict]:
    try:
        with crud.session_scope() as s:
            lots = crud.list_lots(db=s)
            if lots:
                return lots
    except Exception:
        pass
    return list(mock_data.MOCK_LOTS.values())


def get_lot(lot_id: str) -> Optional[dict]:
    try:
        with crud.session_scope() as s:
            lot = crud.get_lot(lot_id, db=s)
            if lot:
                return lot
    except Exception:
        pass
    return mock_data.MOCK_LOTS.get(lot_id)


def get_lot_summary(lot_id: str) -> Optional[dict]:
    try:
        with crud.session_scope() as s:
            summary = crud.get_lot_summary(lot_id, db=s)
            if summary:
                return summary
    except Exception:
        pass
    return mock_data.MOCK_LOT_SUMMARIES.get(lot_id)


def get_lot_components(lot_id: str) -> List[dict]:
    try:
        with crud.session_scope() as s:
            db_lot = crud.get_lot(lot_id, db=s)
            if db_lot is not None:
                return crud.get_lot_components(lot_id, db=s)
    except Exception:
        pass
    if lot_id in mock_data.MOCK_LOTS:
        return [
            c for c in mock_data.MOCK_COMPONENTS.values()
            if c.get("lot_id") == lot_id
        ]
    return []
