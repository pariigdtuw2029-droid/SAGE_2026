from typing import List, Optional

from pydantic import BaseModel, Field

from app.schemas.inference import InferenceTraceResponse
from app.schemas.review import ReviewResponse


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
    drift_score: Optional[float] = None
    reliability_index: Optional[float] = None
    model_version: Optional[str] = None
    split: Optional[str] = None
    inference_run_id: Optional[str] = Field(
        default=None, description="Linked Phase 17 inference run ID"
    )
    inference_trace: Optional[InferenceTraceResponse] = Field(
        default=None, description="Inference execution trace and model provenance"
    )


class TrajectoryPoint(BaseModel):
    time: float = Field(description="Hours since burn-in start")
    value: float


class TrajectoryResponse(BaseModel):
    component_id: str
    actual: List[TrajectoryPoint]
    predicted: List[TrajectoryPoint]
    safety_limit: Optional[float] = None


class ExplanationItem(BaseModel):
    feature: str
    contribution: Optional[float] = None
    reason: str
    module: Optional[str] = Field(default=None, description="Generating module (MODULE_A, MODULE_B, MODULE_C)")
    evidence_type: Optional[str] = Field(default=None, description="Classification of evidence (SHAP, DRIFT, RULE, FALLBACK)")
    run_id: Optional[str] = Field(default=None, description="Linked inference run ID")


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
    drift_score: Optional[float] = None
    predicted_drift_score: Optional[float] = None
    reliability_index: Optional[float] = None
    lot_relative_score: Optional[float] = None
    multivariate_score: Optional[float] = None
    worst_lot_zscore: Optional[float] = None
    absolute_spec_fail: Optional[int] = None
    interval_low: Optional[float] = None
    interval_high: Optional[float] = None
    uncertainty_score: Optional[float] = None
    explanations: Optional[List[ExplanationItem]] = Field(
        default_factory=list,
        description="Structured feature-level explanations and attributions",
    )
    inference_run_id: Optional[str] = Field(
        default=None, description="Linked Phase 17 inference run ID"
    )
    inference_trace: Optional[InferenceTraceResponse] = Field(
        default=None, description="Inference execution trace and model provenance"
    )
    current_disposition: Optional[str] = Field(
        default=None,
        description="Latest human engineering review disposition (PASS, HOLD, QUARANTINE)",
    )
    total_reviews: Optional[int] = Field(
        default=0,
        description="Total number of human engineering review records",
    )
    reviews: Optional[List[ReviewResponse]] = Field(
        default_factory=list,
        description="Chronological engineering review history",
    )
