from typing import List, Optional

from pydantic import BaseModel, Field


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