import logging

from fastapi import APIRouter, File, HTTPException, UploadFile

from app.schemas.upload import UploadResponse
from app.services import upload_service

logger = logging.getLogger("astra_guard.upload")

router = APIRouter(
    prefix="/api/burnin",
    tags=["Burn-in Upload"]
)


@router.post(
    "/upload",
    response_model=UploadResponse,
    responses={
        400: {"description": "Invalid file (wrong type, empty, or too large)"},
        500: {"description": "Unexpected server error"},
    },
)
async def upload_burnin_file(file: UploadFile = File(...)):
    """
    Accept a burn-in test CSV file.

    Performs API-layer validation only (file type, emptiness, size).
    Parsing/cleaning/storage is Member 2's responsibility once the
    database layer is connected; for now this endpoint confirms the
    upload is well-formed and hands back an acknowledgement.
    """
    name_check = upload_service.validate_filename(file.filename)
    if not name_check.is_valid:
        logger.warning("Upload rejected for filename '%s': %s", file.filename, name_check.error)
        raise HTTPException(status_code=400, detail=name_check.error)

    try:
        content = await file.read()
    except Exception:
        # Don't leak internals; surface a generic failure.
        logger.exception("Failed to read uploaded file '%s'", file.filename)
        raise HTTPException(status_code=500, detail="Failed to read uploaded file.")

    content_check = upload_service.validate_content(content)
    if not content_check.is_valid:
        logger.warning("Upload rejected for content in '%s': %s", file.filename, content_check.error)
        raise HTTPException(status_code=400, detail=content_check.error)

    logger.info(
        "Upload accepted: '%s' (%d bytes)",
        file.filename,
        len(content),
    )
    return UploadResponse(
        message="File received successfully",
        filename=file.filename,
        status="success",
    )
