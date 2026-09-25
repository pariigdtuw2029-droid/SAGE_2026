"""
Engineering review service: review persistence, validation, and history queries.
SAGE / SIH 2026 — Semiconductor Burn-In & Latent Defect Screening Console
Team: BINARY BADDIES
"""

import logging
from typing import List, Optional, Tuple

from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from app.models.review import ComponentReview, MAX_COMMENT_LENGTH, VALID_DISPOSITIONS
from app.services import audit_service, component_service
from database.connection import SessionLocal, engine

logger = logging.getLogger("sage.review_service")


def init_reviews_db(target_engine=None) -> None:
    """
    Ensure the component_reviews table exists in the current database engine.
    Additive, safe, and idempotent.
    """
    eng = target_engine or engine
    try:
        ComponentReview.__table__.create(bind=eng, checkfirst=True)
        logger.info("Initialized component_reviews table successfully.")
    except Exception as exc:
        logger.warning("Reviews DB initialization notice: %s", exc)


def create_component_review(
    component_id: str,
    user_id: int,
    username: str,
    role: str,
    disposition: str,
    comment: Optional[str] = None,
    client_ip: Optional[str] = None,
) -> Tuple[Optional[ComponentReview], Optional[str]]:
    """
    Record an authorized engineering disposition against a component.
    - Validates component existence.
    - Validates disposition state (PASS, HOLD, QUARANTINE).
    - Validates comment length (<= 1000 chars).
    - Preserves historical records (append-only history).
    - Audits the event via REVIEW_DISPOSITION.
    Returns: (review_record, error_message)
    """
    clean_id = (component_id or "").strip()
    if not clean_id:
        return None, "Invalid component ID."

    # Validate component exists in domain
    comp = component_service.get_component(clean_id)
    if comp is None:
        return None, f"Component '{clean_id}' not found."

    clean_disp = (disposition or "").strip().upper()
    if clean_disp not in VALID_DISPOSITIONS:
        return None, f"Invalid disposition '{disposition}'. Must be one of: {sorted(VALID_DISPOSITIONS)}"

    clean_comment = comment.strip() if comment else None
    if clean_comment and len(clean_comment) > MAX_COMMENT_LENGTH:
        return None, f"Comment exceeds maximum allowed length of {MAX_COMMENT_LENGTH} characters."

    try:
        with SessionLocal() as db:
            review = ComponentReview(
                component_id=clean_id,
                user_id=user_id,
                username=username,
                role=role,
                disposition=clean_disp,
                comment=clean_comment,
            )
            db.add(review)
            db.commit()
            db.refresh(review)

            # Record audit event (decoupled transaction)
            audit_service.record_audit_event(
                event_type="REVIEW_DISPOSITION",
                event_status="SUCCESS",
                endpoint=f"/api/components/{clean_id}/review",
                http_method="POST",
                user_id=user_id,
                username=username,
                role=role,
                client_ip=client_ip or "127.0.0.1",
                target_entity_type="component",
                target_entity_id=clean_id,
                details={
                    "component_id": clean_id,
                    "disposition": clean_disp,
                    "review_id": review.id,
                    "has_comment": bool(clean_comment),
                },
            )
            return review, None
    except Exception as exc:
        logger.exception("Failed to persist component review for '%s': %s", clean_id, exc)
        audit_service.record_audit_event(
            event_type="REVIEW_DISPOSITION",
            event_status="FAILURE",
            endpoint=f"/api/components/{clean_id}/review",
            http_method="POST",
            user_id=user_id,
            username=username,
            role=role,
            client_ip=client_ip or "127.0.0.1",
            target_entity_type="component",
            target_entity_id=clean_id,
            details={"error": "Database persistence failure"},
        )
        return None, "Internal error while recording disposition."


def get_component_reviews(
    component_id: str,
) -> Optional[Tuple[Optional[str], int, List[ComponentReview]]]:
    """
    Retrieve review history for a component in reverse chronological order.
    Returns: (current_disposition, total_reviews, reviews_list) or None if component not found.
    """
    clean_id = (component_id or "").strip()
    if not clean_id:
        return None

    # Check component existence
    comp = component_service.get_component(clean_id)
    if comp is None:
        return None

    with SessionLocal() as db:
        stmt = (
            select(ComponentReview)
            .where(ComponentReview.component_id == clean_id)
            .order_by(desc(ComponentReview.created_at), desc(ComponentReview.id))
        )
        reviews = list(db.scalars(stmt).all())
        total = len(reviews)
        current_disp = reviews[0].disposition if reviews else None
        return current_disp, total, reviews
