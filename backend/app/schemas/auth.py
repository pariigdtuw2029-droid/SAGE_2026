"""
Pydantic schemas for authentication requests and responses.
SAGE / SIH 2026 — Semiconductor Burn-In & Latent Defect Screening Console
Team: BINARY BADDIES
"""

from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field


class UserRole(str, Enum):
    ADMIN = "admin"
    ENGINEER = "engineer"
    REVIEWER = "reviewer"


class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=64, description="User login identifier")
    password: str = Field(..., min_length=1, description="Plaintext password for verification")


class TokenResponse(BaseModel):
    access_token: str = Field(..., description="Signed JWT Bearer access token")
    token_type: str = Field(default="bearer", description="Token type, always bearer")
    expires_in: int = Field(..., description="Access token expiration window in seconds")


class UserResponse(BaseModel):
    id: Optional[int] = Field(default=None, description="Internal user ID")
    username: str = Field(..., description="Authenticated username")
    full_name: Optional[str] = Field(default=None, description="Full name or display title")
    role: str = Field(default="engineer", description="Assigned role: admin, engineer, or reviewer")
    is_active: bool = Field(default=True, description="Account active status")

