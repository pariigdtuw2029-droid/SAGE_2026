"""
Audit service layer: Event recording, privacy sanitization, and audit queries.
SAGE / SIH 2026 — Semiconductor Burn-In & Latent Defect Screening Console
Team: BINARY BADDIES
"""

import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple, Union

from fastapi import Request
from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from app.models.audit_log import AuditLog, VALID_EVENT_STATUSES, VALID_EVENT_TYPES
from database.connection import SessionLocal, engine

logger = logging.getLogger("sage.audit_service")

# Sensitive fields that must NEVER appear in audit records
PROHIBITED_FIELD_NAMES = {
    "password",
    "hashed_password",
    "token",
    "access_token",
    "refresh_token",
    "authorization",
    "secret",
    "sage_jwt_secret",
    "raw_content",
    "cookie",
    "cookies",
    "session",
    "credentials",
}

# Regex to scrub any accidental Bearer tokens or JWT-like strings in values
JWT_PATTERN = re.compile(r"Bearer\s+[A-Za-z0-9-_=]+\.[A-Za-z0-9-_=]+\.?[A-Za-z0-9-_.+/=]*", re.IGNORECASE)


def sanitize_value(val: Any) -> Any:
    """Recursively sanitize data structures to strip secrets and scrub tokens."""
    if isinstance(val, dict):
        cleaned = {}
        for k, v in val.items():
            if str(k).lower().strip() in PROHIBITED_FIELD_NAMES:
                continue
            cleaned[k] = sanitize_value(v)
        return cleaned
    elif isinstance(val, list):
        return [sanitize_value(item) for item in val]
    elif isinstance(val, str):
        # Redact any Bearer tokens
        return JWT_PATTERN.sub("[REDACTED_BEARER_TOKEN]", val)
    return val


def sanitize_details(details: Optional[Union[str, Dict[str, Any]]]) -> Optional[str]:
    """Sanitize and JSON-serialize the details field for audit storage."""
    if details is None:
        return None
    if isinstance(details, dict):
        cleaned = sanitize_value(details)
        return json.dumps(cleaned, default=str)
    if isinstance(details, str):
        # Scrub any accidental tokens
        scrubbed = JWT_PATTERN.sub("[REDACTED_BEARER_TOKEN]", details)
        return scrubbed
    return json.dumps(sanitize_value(details), default=str)


def get_client_ip(request: Optional[Request]) -> str:
    """Extract client IP from FastAPI Request or fallback to 127.0.0.1."""
    if request and request.client and request.client.host:
        return request.client.host
    return "127.0.0.1"


def init_audit_db(target_engine=None) -> None:
    """
    Ensure the audit_logs table exists in the current database engine.
    Additive, safe, and idempotent.
    """
    eng = target_engine or engine
    try:
        AuditLog.__table__.create(bind=eng, checkfirst=True)
        logger.info("Initialized audit_logs table successfully.")
    except Exception as exc:
        logger.warning("Audit DB initialization notice: %s", exc)


def record_audit_event(
    event_type: str,
    event_status: str,
    endpoint: str,
    http_method: str,
    user_id: Optional[int] = None,
    username: Optional[str] = None,
    role: Optional[str] = None,
    target_entity_type: Optional[str] = None,
    target_entity_id: Optional[str] = None,
    details: Optional[Union[str, Dict[str, Any]]] = None,
    client_ip: Optional[str] = None,
    db_session: Optional[Session] = None,
) -> Optional[AuditLog]:
    """
    Record an append-only audit event in an isolated database transaction.
    Guarantees:
    - Never raises unhandled exceptions to disrupt calling business logic.
    - Decoupled from primary domain transactions.
    - Strictly validates event_type and event_status against allowed sets.
    - Sanitizes details to ensure no credentials, tokens, or raw datasets leak.
    """
    if event_type not in VALID_EVENT_TYPES:
        logger.warning("Invalid audit event_type rejected: '%s'", event_type)
        return None

    if event_status not in VALID_EVENT_STATUSES:
        logger.warning("Invalid audit event_status rejected: '%s'", event_status)
        return None

    safe_details = sanitize_details(details)

    try:
        if db_session is not None:
            log_entry = AuditLog(
                user_id=user_id,
                username=username,
                role=role,
                event_type=event_type,
                event_status=event_status,
                endpoint=endpoint,
                http_method=http_method,
                target_entity_type=target_entity_type,
                target_entity_id=str(target_entity_id) if target_entity_id is not None else None,
                details=safe_details,
                client_ip=client_ip or "127.0.0.1",
            )
            db_session.add(log_entry)
            db_session.flush()
            return log_entry
        else:
            with SessionLocal() as session:
                log_entry = AuditLog(
                    user_id=user_id,
                    username=username,
                    role=role,
                    event_type=event_type,
                    event_status=event_status,
                    endpoint=endpoint,
                    http_method=http_method,
                    target_entity_type=target_entity_type,
                    target_entity_id=str(target_entity_id) if target_entity_id is not None else None,
                    details=safe_details,
                    client_ip=client_ip or "127.0.0.1",
                )
                session.add(log_entry)
                session.commit()
                session.refresh(log_entry)
                return log_entry
    except Exception as exc:
        # Visible in backend diagnostics; does not silently hide, but does not corrupt domain transaction
        logger.exception("Failed to write audit log event [%s / %s]: %s", event_type, event_status, exc)
        return None


def get_audit_logs(
    db: Optional[Session] = None,
    limit: int = 100,
    offset: int = 0,
    event_type: Optional[str] = None,
    username: Optional[str] = None,
) -> Tuple[int, List[AuditLog]]:
    """Query audit logs in read-only mode with pagination and optional filtering."""
    def _execute_query(session: Session) -> Tuple[int, List[AuditLog]]:
        stmt = select(AuditLog)
        count_stmt = select(func.count(AuditLog.id))

        if event_type:
            stmt = stmt.where(AuditLog.event_type == event_type.strip().upper())
            count_stmt = count_stmt.where(AuditLog.event_type == event_type.strip().upper())
        if username:
            stmt = stmt.where(AuditLog.username == username.strip())
            count_stmt = count_stmt.where(AuditLog.username == username.strip())

        total = session.scalar(count_stmt) or 0
        items = list(session.scalars(stmt.order_by(desc(AuditLog.timestamp), desc(AuditLog.id)).offset(offset).limit(limit)).all())
        return total, items

    if db is not None:
        return _execute_query(db)
    with SessionLocal() as session:
        return _execute_query(session)
