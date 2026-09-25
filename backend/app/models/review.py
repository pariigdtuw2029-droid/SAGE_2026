"""
ComponentReview ORM model for human engineering review & disposition workflow.
SAGE / SIH 2026 — Semiconductor Burn-In & Latent Defect Screening Console
Team: BINARY BADDIES
"""

from datetime import datetime, timezone
from sqlalchemy import DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from database.connection import Base

# Strictly constrained human disposition states for Phase 15
VALID_DISPOSITIONS = {"PASS", "HOLD", "QUARANTINE"}
MAX_COMMENT_LENGTH = 1000


class ComponentReview(Base):
    __tablename__ = "component_reviews"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    component_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    username: Mapped[str] = mapped_column(String(64), nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    disposition: Mapped[str] = mapped_column(String(16), nullable=False)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
        index=True,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
