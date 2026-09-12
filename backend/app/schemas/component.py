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
    slope_reject_flag: Optional[bool] = Field(
        default=None,
        description="True if predicted 168h drift rate exceeds Safe-population threshold",
    )
    reliability_tier: Optional[str] = Field(
        default=None,
        description="Assigned reliability tier (Space-Safe, Borderline, or High-Risk)",
    )


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
    reasons: List[str] = Field(
        default_factory=list,
        description="Explanations for the risk call (physics-based and [SHAP] attributions)",
    )
    model_version: Optional[str] = None
    slope_reject_flag: Optional[bool] = Field(
        default=None,
        description="True if predicted 168h drift rate exceeds Safe-population threshold",
    )
    reliability_tier: Optional[str] = Field(
        default=None,
        description="Assigned reliability tier (Space-Safe, Borderline, or High-Risk)",
    )
