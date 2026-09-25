from fastapi import APIRouter, Depends, HTTPException, Request

from app.api.auth import get_current_user, require_roles
from app.models.user import User
from app.schemas.component import (
    ComponentReportResponse,
    ComponentResponse,
    TrajectoryResponse,
)
from app.schemas.review import (
    ComponentReviewHistoryResponse,
    ReviewCreateRequest,
    ReviewResponse,
)
from app.services import audit_service, component_service, review_service

router = APIRouter(
    prefix="/api/components",
    tags=["Components"],
    dependencies=[Depends(get_current_user)],
)


@router.get("/{component_id}", response_model=ComponentResponse)
def get_component(component_id: str):
    clean_id = component_id.strip()
    if not clean_id:
        raise HTTPException(status_code=400, detail="Invalid component ID.")
    component = component_service.get_component(clean_id)
    if component is None:
        raise HTTPException(
            status_code=404, detail=f"Component '{clean_id}' not found."
        )
    return component


@router.get("/{component_id}/trajectory", response_model=TrajectoryResponse)
def get_component_trajectory(component_id: str):
    clean_id = component_id.strip()
    if not clean_id:
        raise HTTPException(status_code=400, detail="Invalid component ID.")
    trajectory = component_service.get_trajectory(clean_id)
    if trajectory is None:
        raise HTTPException(
            status_code=404, detail=f"Component '{clean_id}' not found."
        )
    return trajectory


@router.get(
    "/{component_id}/report",
    response_model=ComponentReportResponse,
    tags=["Reports"],
    summary="Get detailed component engineering screening report",
)
def get_component_report(
    component_id: str,
    current_user: User = Depends(get_current_user),
):
    """
    Retrieve detailed component engineering screening and risk assessment report.
    Accessible to all authenticated roles (admin, engineer, reviewer).
    """
    clean_id = component_id.strip()
    if not clean_id:
        raise HTTPException(status_code=400, detail="Invalid component ID.")
    report = component_service.get_report(clean_id)
    if report is None:
        raise HTTPException(
            status_code=404, detail=f"Component '{clean_id}' not found."
        )
    return report


@router.post(
    "/{component_id}/review",
    response_model=ReviewResponse,
    summary="Record human engineering disposition for a component",
)
def submit_component_review(
    component_id: str,
    payload: ReviewCreateRequest,
    req: Request,
    current_user: User = Depends(require_roles("admin", "engineer")),
):
    """
    Authorized human review disposition recording.
    Restricted to admin and engineer roles (reviewer receives 403 Forbidden).
    Preserves historical review records and logs REVIEW_DISPOSITION audit event.
    """
    clean_id = component_id.strip()
    if not clean_id:
        raise HTTPException(status_code=400, detail="Invalid component ID.")

    client_ip = audit_service.get_client_ip(req)
    review, err = review_service.create_component_review(
        component_id=clean_id,
        user_id=current_user.id,
        username=current_user.username,
        role=current_user.role,
        disposition=payload.disposition,
        comment=payload.comment,
        client_ip=client_ip,
    )
    if err:
        if "not found" in err.lower():
            raise HTTPException(status_code=404, detail=err)
        raise HTTPException(status_code=400, detail=err)

    return ReviewResponse.model_validate(review)


@router.get(
    "/{component_id}/reviews",
    response_model=ComponentReviewHistoryResponse,
    summary="Get human engineering review history for a component",
)
def get_component_reviews(
    component_id: str,
    current_user: User = Depends(get_current_user),
):
    """
    Retrieve chronological engineering review history for a component.
    Accessible to all authenticated roles (admin, engineer, reviewer).
    """
    clean_id = component_id.strip()
    if not clean_id:
        raise HTTPException(status_code=400, detail="Invalid component ID.")

    result = review_service.get_component_reviews(clean_id)
    if result is None:
        raise HTTPException(status_code=404, detail=f"Component '{clean_id}' not found.")

    current_disp, total, reviews = result
    return ComponentReviewHistoryResponse(
        component_id=clean_id,
        current_disposition=current_disp,
        total_reviews=total,
        reviews=[ReviewResponse.model_validate(r) for r in reviews],
    )

