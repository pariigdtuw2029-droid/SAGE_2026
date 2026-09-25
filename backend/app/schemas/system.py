from typing import Dict, Optional

from pydantic import BaseModel, Field


class DatabaseRecordCounts(BaseModel):
    lots: int = Field(default=0, description="Total ingested lots")
    components: int = Field(default=0, description="Total component records")
    measurements: int = Field(default=0, description="Total physical parameter measurements")
    predictions: int = Field(default=0, description="Total 168h drift predictions")
    risk_assessments: int = Field(default=0, description="Total risk assessment records")
    explanations: int = Field(default=0, description="Total SHAP explanation records")


class DatabaseStatus(BaseModel):
    connected: bool = Field(description="Database connectivity status")
    records: Optional[DatabaseRecordCounts] = Field(
        default=None,
        description="Current row counts per table (omitted if database is unreachable)",
    )


class ModelStackStatus(BaseModel):
    status: str = Field(description="Operational readiness: ready | degraded | unavailable")
    version: Optional[str] = Field(default=None, description="Active model stack version")
    module_a: str = Field(description="Early anomaly detection module: loaded | unavailable")
    module_b: str = Field(description="168h degradation forecast module: loaded | unavailable")
    module_c: str = Field(description="Reliability and decision fusion module: active | unavailable")
    conformal_prediction: Optional[str] = Field(
        default=None, description="Conformal prediction interval status: enabled | unavailable"
    )


class SystemStatusResponse(BaseModel):
    status: str = Field(description="Overall system health status: healthy | degraded | unavailable")
    database: DatabaseStatus = Field(description="Database connectivity and table volume diagnostics")
    model_stack: ModelStackStatus = Field(description="Sanitized ML model stack availability")
