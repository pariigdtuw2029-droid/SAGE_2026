"""
Lot service layer.

Routers call these functions and never touch storage directly. Today
these read from `mock_data` (demo data, clearly isolated). Member 2
can replace the bodies below with real SQLAlchemy/PostgreSQL queries
without requiring any change to `app/api/lots.py`.
"""

from typing import List, Optional

from app.services import mock_data


def list_lots() -> List[dict]:
    return list(mock_data.MOCK_LOTS.values())


def get_lot(lot_id: str) -> Optional[dict]:
    return mock_data.MOCK_LOTS.get(lot_id)


def get_lot_summary(lot_id: str) -> Optional[dict]:
    return mock_data.MOCK_LOT_SUMMARIES.get(lot_id)
