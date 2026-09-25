"""
Pydantic schemas for audit logs.
SAGE / SIH 2026 — Semiconductor Burn-In & Latent Defect Screening Console
Team: BINARY BADDIES
"""

from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel, ConfigDict


class AuditLogResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: Optional[int] = None
    username: Optional[str] = None
    role: Optional[str] = None
    event_type: str
    event_status: str
    timestamp: datetime
    endpoint: str
    http_method: str
    target_entity_type: Optional[str] = None
    target_entity_id: Optional[str] = None
    details: Optional[str] = None
    client_ip: Optional[str] = None


class AuditLogListResponse(BaseModel):
    total: int
    items: List[AuditLogResponse]
