"""
Upload service layer.

Owns API-layer validation of an uploaded burn-in CSV file:
- extension / content-type check
- empty-file check
- size-limit check

Deliberately does NOT parse, clean, or interpret the CSV contents —
that is Member 2's preprocessing responsibility. This layer only
decides whether the upload is well-formed enough to hand off.
"""

from dataclasses import dataclass
from typing import Optional

MAX_UPLOAD_SIZE_BYTES = 10 * 1024 * 1024  # 10 MB, generous for a hackathon CSV
ALLOWED_EXTENSIONS = (".csv",)


@dataclass
class UploadValidationResult:
    is_valid: bool
    error: Optional[str] = None


def validate_filename(filename: Optional[str]) -> UploadValidationResult:
    if not filename:
        return UploadValidationResult(False, "No filename provided.")
    if not filename.lower().endswith(ALLOWED_EXTENSIONS):
        return UploadValidationResult(
            False, "Unsupported file type. Only .csv files are accepted."
        )
    return UploadValidationResult(True)


def validate_content(content: bytes) -> UploadValidationResult:
    if content is None or len(content) == 0:
        return UploadValidationResult(False, "Uploaded file is empty.")
    if len(content) > MAX_UPLOAD_SIZE_BYTES:
        return UploadValidationResult(
            False,
            f"File exceeds the maximum allowed size of "
            f"{MAX_UPLOAD_SIZE_BYTES // (1024 * 1024)} MB.",
        )
    return UploadValidationResult(True)
