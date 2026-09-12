from fastapi import APIRouter, HTTPException

from app.schemas.component import (
    ComponentReportResponse,
    ComponentResponse,
    TrajectoryResponse,
)
from app.services import component_service

router = APIRouter(prefix="/api/components", tags=["Components"])


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
)
def get_component_report(component_id: str):
    clean_id = component_id.strip()
    if not clean_id:
        raise HTTPException(status_code=400, detail="Invalid component ID.")
    report = component_service.get_report(clean_id)
    if report is None:
        raise HTTPException(
            status_code=404, detail=f"Component '{clean_id}' not found."
        )
    return report
