from typing import List

from fastapi import APIRouter

from app.schemas.alert import AlertResponse
from app.services import alert_service

router = APIRouter(prefix="/api/alerts", tags=["Alerts"])


@router.get("", response_model=List[AlertResponse])
def get_alerts():
    """High-risk components requiring attention."""
    return alert_service.list_alerts()
