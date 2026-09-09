from typing import List, Optional

from pydantic import BaseModel, Field


class ComponentResponse(BaseModel):
    component_id: str
    lot_id: str
    status: str
    risk_level: str
    anomaly_score: Optional[float] = None
    predicted_168h: Optional[float] = Field(
        default=None, description="Predicted value at the 168h burn-in mark"
    )
    risk_score: Optional[float] = None
    decision: Optional[str] = None
    confidence: Optional[float] = None


class TrajectoryPoint(BaseModel):
    time: float = Field(description="Hours since burn-in start")
    value: float


class TrajectoryResponse(BaseModel):
    component_id: str
    actual: List[TrajectoryPoint]
    predicted: List[TrajectoryPoint]
    safety_limit: Optional[float] = None


class ComponentReportResponse(BaseModel):
    component_id: str
    lot_id: str
    anomaly_score: Optional[float] = None
    drift_risk: Optional[str] = None
    predicted_168h: Optional[float] = None
    risk_score: Optional[float] = None
    decision: str
    confidence: Optional[float] = None
    reasons: List[str] = Field(default_factory=list)
    model_version: Optional[str] = None
