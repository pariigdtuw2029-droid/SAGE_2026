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
    component = component_service.get_component(component_id)
    if component is None:
        raise HTTPException(
            status_code=404, detail=f"Component '{component_id}' not found."
        )
    return component


@router.get("/{component_id}/trajectory", response_model=TrajectoryResponse)
def get_component_trajectory(component_id: str):
    trajectory = component_service.get_trajectory(component_id)
    if trajectory is None:
        raise HTTPException(
            status_code=404, detail=f"Component '{component_id}' not found."
        )
    return trajectory


@router.get(
    "/{component_id}/report",
    response_model=ComponentReportResponse,
    tags=["Reports"],
)
def get_component_report(component_id: str):
    report = component_service.get_report(component_id)
    if report is None:
        raise HTTPException(
            status_code=404, detail=f"Component '{component_id}' not found."
        )
    return report
