from pydantic import BaseModel


class AlertResponse(BaseModel):
    component_id: str
    lot_id: str
    risk_score: float
    severity: str
