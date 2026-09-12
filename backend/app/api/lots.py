from typing import List

from fastapi import APIRouter, HTTPException

from app.schemas.component import ComponentResponse
from app.schemas.lot import LotResponse, LotSummaryResponse
from app.services import lot_service

router = APIRouter(prefix="/api/lots", tags=["Lots"])


@router.get("", response_model=List[LotResponse])
def get_lots():
    """List all lots. Backed by demo data until Member 2's DB layer is connected."""
    return lot_service.list_lots()


@router.get("/{lot_id}", response_model=LotResponse)
def get_lot(lot_id: str):
    clean_id = lot_id.strip()
    if not clean_id:
        raise HTTPException(status_code=400, detail="Invalid lot ID.")
    lot = lot_service.get_lot(clean_id)
    if lot is None:
        raise HTTPException(status_code=404, detail=f"Lot '{clean_id}' not found.")
    return lot


@router.get("/{lot_id}/summary", response_model=LotSummaryResponse)
def get_lot_summary(lot_id: str):
    clean_id = lot_id.strip()
    if not clean_id:
        raise HTTPException(status_code=400, detail="Invalid lot ID.")
    summary = lot_service.get_lot_summary(clean_id)
    if summary is None:
        raise HTTPException(
            status_code=404, detail=f"Summary for lot '{clean_id}' not found."
        )
    return summary


@router.get("/{lot_id}/components", response_model=List[ComponentResponse])
def get_lot_components(lot_id: str):
    clean_id = lot_id.strip()
    if not clean_id:
        raise HTTPException(status_code=400, detail="Invalid lot ID.")
    lot = lot_service.get_lot(clean_id)
    if lot is None:
        raise HTTPException(
            status_code=404, detail=f"Lot '{clean_id}' not found."
        )
    return lot_service.get_lot_components(clean_id)
