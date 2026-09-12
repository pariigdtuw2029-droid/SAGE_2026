"""
Alert service layer. Same boundary contract as the other services.
Database-first: queries Member 2's database layer via CRUD, falling back
to mock data when the database returns no alerts.
"""

from typing import List

from app.services import mock_data
from database import crud
from database.connection import init_db

# Ensure database tables exist if running against a fresh database file.
try:
    init_db()
except Exception:
    pass


def list_alerts() -> List[dict]:
    try:
        with crud.session_scope() as s:
            counts = crud.count_rows(db=s)
            if counts.get("components", 0) > 0:
                return crud.get_alerts(db=s)
    except Exception:
        pass
    return mock_data.list_alerts()
