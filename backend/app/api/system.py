import logging
from typing import Optional

from fastapi import APIRouter, Depends, Query

from app.api.auth import get_current_user, require_roles
from app.models.user import User
from app.schemas.audit import AuditLogListResponse, AuditLogResponse
from app.schemas.system import (
    DatabaseRecordCounts,
    DatabaseStatus,
    ModelStackStatus,
    SystemStatusResponse,
)
from app.services import audit_service
from database import crud
from database.ml_inference import describe_stack

logger = logging.getLogger("sage.system")

router = APIRouter(
    prefix="/api/system",
    tags=["System"],
    dependencies=[Depends(get_current_user)],
)


@router.get("/status", response_model=SystemStatusResponse)
def get_system_status() -> SystemStatusResponse:
    """
    Sanitized, read-only system and diagnostic status.

    Retrieves database connectivity and aggregate table counts from existing
    CRUD utilities, and checks model stack availability using cached artifact
    descriptors. Never performs inference or mutates system state.
    """
    # 1. Database diagnostics
    db_connected = False
    records: Optional[DatabaseRecordCounts] = None
    try:
        with crud.session_scope() as s:
            counts = crud.count_rows(db=s)
            db_connected = True
            records = DatabaseRecordCounts(
                lots=counts.get("lots", 0),
                components=counts.get("components", 0),
                measurements=counts.get("measurements", 0),
                predictions=counts.get("predictions", 0),
                risk_assessments=counts.get("risk_assessments", 0),
                explanations=counts.get("explanations", 0),
            )
    except Exception as exc:
        logger.warning("Database status check failed: %s", exc)
        db_connected = False
        records = None

    # 2. Model stack diagnostics
    mod_a_loaded = False
    mod_b_loaded = False
    mod_c_active = False
    conformal_enabled = False
    stack_version: Optional[str] = None

    try:
        raw_stack = describe_stack()
        stack_version = raw_stack.get("stack_version")
        mod_a_loaded = bool(raw_stack.get("module_a", {}).get("available", False))
        mod_b_loaded = bool(raw_stack.get("module_b", {}).get("available", False))
        mod_c_active = bool(raw_stack.get("module_c"))
        if mod_b_loaded:
            conformal_enabled = True
    except Exception as exc:
        logger.warning("Model stack status check failed: %s", exc)

    if mod_a_loaded and mod_b_loaded and mod_c_active:
        model_status = "ready"
    elif mod_a_loaded or mod_b_loaded or mod_c_active:
        model_status = "degraded"
    else:
        model_status = "unavailable"

    model_stack = ModelStackStatus(
        status=model_status,
        version=stack_version,
        module_a="loaded" if mod_a_loaded else "unavailable",
        module_b="loaded" if mod_b_loaded else "unavailable",
        module_c="active" if mod_c_active else "unavailable",
        conformal_prediction="enabled" if conformal_enabled else "unavailable",
    )

    # 3. Overall health semantics
    if db_connected and model_status == "ready":
        overall_status = "healthy"
    elif db_connected or model_status in ("ready", "degraded"):
        overall_status = "degraded"
    else:
        overall_status = "unavailable"

    return SystemStatusResponse(
        status=overall_status,
        database=DatabaseStatus(connected=db_connected, records=records),
        model_stack=model_stack,
    )


@router.get("/audit-logs", response_model=AuditLogListResponse)
def list_audit_logs(
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    event_type: Optional[str] = None,
    username: Optional[str] = None,
    current_user: User = Depends(require_roles("admin")),
):
    """
    Administrative read-only audit log endpoint.
    Restricted strictly to admin role.
    Append-only: No modification or deletion endpoints exist for audit logs.
    """
    total, items = audit_service.get_audit_logs(
        limit=limit,
        offset=offset,
        event_type=event_type,
        username=username,
    )
    return AuditLogListResponse(
        total=total,
        items=[AuditLogResponse.model_validate(it) for it in items],
    )

