from pydantic import BaseModel


class RiskResponse(BaseModel):
    component_id: str
    risk_score: float
    risk_level: str