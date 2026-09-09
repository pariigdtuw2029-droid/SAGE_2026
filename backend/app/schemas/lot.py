from pydantic import BaseModel, Field


class LotResponse(BaseModel):
    lot_id: str
    total_components: int
    status: str


class LotSummaryResponse(BaseModel):
    """Aggregated stats for a lot, used by the dashboard."""
    lot_id: str
    total_components: int
    passed: int = Field(description="Components that passed screening")
    monitor: int = Field(description="Components flagged for monitoring")
    hold: int = Field(description="Components put on hold pending review")
    reject: int = Field(description="Components rejected")
    average_anomaly_score: float
    high_risk_components: int