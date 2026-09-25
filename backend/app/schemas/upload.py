from typing import List, Optional

from pydantic import BaseModel, Field

from app.schemas.inference import InferenceTraceResponse


class UploadResponse(BaseModel):
    message: str
    filename: str
    status: str
    valid_rows: Optional[int] = None
    rejected_rows: Optional[int] = None
    lots_created: Optional[int] = None
    components_created: Optional[int] = None
    predictions_created: Optional[int] = None
    risk_assessments_created: Optional[int] = None
    model_version: Optional[str] = None
    warnings: Optional[List[str]] = Field(default_factory=list)
    data_quality: Optional[dict] = None
    inference_run_id: Optional[str] = Field(
        default=None, description="Unique UUID identifier for this inference run"
    )
    inference_trace: Optional[InferenceTraceResponse] = Field(
        default=None, description="Inference execution trace and model provenance"
    )
