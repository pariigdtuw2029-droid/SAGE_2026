"""
Pydantic schemas for engineering review & disposition workflow.
SAGE / SIH 2026 — Semiconductor Burn-In & Latent Defect Screening Console
Team: BINARY BADDIES
"""

from datetime import datetime
from enum import Enum
from typing import List, Optional
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.review import MAX_COMMENT_LENGTH, VALID_DISPOSITIONS


class DispositionEnum(str, Enum):
    PASS = "PASS"
    HOLD = "HOLD"
    QUARANTINE = "QUARANTINE"


class ReviewCreateRequest(BaseModel):
    disposition: str = Field(..., description="Engineering disposition (PASS, HOLD, QUARANTINE)")
    comment: Optional[str] = Field(None, max_length=MAX_COMMENT_LENGTH, description="Optional engineering justification note")

    @field_validator("disposition")
    @classmethod
    def validate_disposition(cls, v: str) -> str:
        v_clean = v.strip().upper() if v else ""
        if v_clean not in VALID_DISPOSITIONS:
            raise ValueError(f"Invalid disposition '{v}'. Must be one of: {sorted(VALID_DISPOSITIONS)}")
        return v_clean

    @field_validator("comment")
    @classmethod
    def validate_comment(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            v_clean = v.strip()
            if len(v_clean) > MAX_COMMENT_LENGTH:
                raise ValueError(f"Comment exceeds maximum length of {MAX_COMMENT_LENGTH} characters.")
            return v_clean if v_clean else None
        return None


class ReviewResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    component_id: str
    user_id: int
    username: str
    role: str
    disposition: str
    comment: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class ComponentReviewHistoryResponse(BaseModel):
    component_id: str
    current_disposition: Optional[str] = None
    total_reviews: int
    reviews: List[ReviewResponse]
