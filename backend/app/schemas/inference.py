"""
Inference traceability and model provenance schemas.
SAGE / SIH 2026 — Semiconductor Burn-In & Latent Defect Screening Console
Team: BINARY BADDIES
"""

from datetime import datetime
from typing import Dict, Optional

from pydantic import BaseModel, Field


class InferenceTraceResponse(BaseModel):
    run_id: str = Field(description="Unique UUID identifier for this inference run")
    timestamp: datetime = Field(description="Execution timestamp (UTC)")
    model_version: str = Field(description="Active model stack version (e.g., sage-1.1 or fallback-stats)")
    execution_status: str = Field(description="Screening run status: SUCCESS | DEGRADED | FALLBACK")
    module_a_status: str = Field(description="Module A status: loaded | fallback | unavailable")
    module_b_status: str = Field(description="Module B status: loaded | degraded | unavailable")
    module_c_status: str = Field(description="Module C status: active | unavailable")
    is_fallback: bool = Field(description="True if Module A operated in statistical fallback")
    artifact_hashes: Dict[str, str] = Field(description="Cryptographic SHA-256 hashes of active model artifacts")
    config_hash: str = Field(description="Cryptographic SHA-256 hash of active config.json")
    source_filename: Optional[str] = Field(default=None, description="Uploaded source filename")
    total_components: int = Field(default=0, description="Total components processed in this run")
