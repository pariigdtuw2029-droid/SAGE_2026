"""
Audit log ORM model for append-only audit trail.
SAGE / SIH 2026 — Semiconductor Burn-In & Latent Defect Screening Console
Team: BINARY BADDIES
"""

from datetime import datetime, timezone
from sqlalchemy import DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from database.connection import Base

# Strictly constrained event types and statuses
VALID_EVENT_TYPES = {"LOGIN", "LOGOUT", "BURNIN_UPLOAD", "PERMISSION_FAILURE", "REVIEW_DISPOSITION"}
VALID_EVENT_STATUSES = {"SUCCESS", "FAILURE"}


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    username: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    role: Mapped[str | None] = mapped_column(String(32), nullable=True)
    event_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    event_status: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
        index=True,
    )
    endpoint: Mapped[str] = mapped_column(String(255), nullable=False)
    http_method: Mapped[str] = mapped_column(String(16), nullable=False)
    target_entity_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    target_entity_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    details: Mapped[str | None] = mapped_column(Text, nullable=True)
    client_ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
