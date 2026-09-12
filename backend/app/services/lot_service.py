"""
Lot service layer.

Routers call these functions and never touch storage directly.
Currently backed by mock data; Member 2's database layer can be
wired here later.
"""

from typing import List, Optional

from app.services import mock_data


def list_lots() -> List[dict]:
    return list(mock_data.MOCK_LOTS.values())


def get_lot(lot_id: str) -> Optional[dict]:
    return mock_data.MOCK_LOTS.get(lot_id)


def get_lot_summary(lot_id: str) -> Optional[dict]:
    return mock_data.MOCK_LOT_SUMMARIES.get(lot_id)
