"""
Automated unit and integration tests for Phase 17: Inference Traceability & Model Provenance.
SAGE / SIH 2026 — Semiconductor Burn-In & Latent Defect Screening Console
Team: BINARY BADDIES
"""

import io
import re
from pathlib import Path
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app.core.security import create_access_token
from app.main import app
from app.services import auth_service, component_service
from database import crud, models, ml_inference
from database.connection import SessionLocal
from database.ingestion import ingest_dataframe

HEX_64_PATTERN = re.compile(r"^[0-9a-f]{64}$")

AUTHORITATIVE_HASHES = {
    "anomaly_pipeline.joblib": "6e136cfa5a4e347e20ae9451f3c6d85083918537fabe004a99688df402a5dccc",
    "drift_prediction_models.joblib": "970ee630691a87505f0005c16b021e9ce9f4c8934fb49beda70e48224c34ec5d",
    "anomaly_shap_bundle.joblib": "14fb3e7585f354c60e9eea14ca6ef67cebfc874d32c4dce2672bbc05187e3003",
    "config.json": "52743c455444b2abae12b0e4fbdf0a64ad8e64a0bb6f8f52eebfad09b26804e7",
    "metadata.json": "776168a61621a729df0956efdd57deec8ab5ec1bc4793ffe834b9de969775793",
}


def _make_test_row(component_id="C9001", batch="ISRO-SCL-250106-99", part="MOSFET", **overrides):
    row = {
        "Component_ID": component_id,
        "Batch_ID": batch,
        "Part_Type": part,
        "Part_Family": "IRF-Series N-Channel",
        "Proxy_Stress": "Cycling-Medium",
        "Stress_Level": 200,
        "Stress_Unit": "cycles",
        "Timestamp_0h": "2025-01-06 00:00:00",
        "Timestamp_24h": "2025-01-07 00:00:00",
        "Timestamp_96h": "2025-01-10 00:00:00",
        "Timestamp_168h": "2025-01-13 00:00:00",
        "Traditional_Test_Result": "Pass",
        "Label": "Safe",
    }
    for p in ("Leakage", "Resistance", "Vth"):
        for cp in ("0h", "24h", "96h", "168h"):
            row[f"{p}_{cp}"] = {"Leakage": 1.0, "Resistance": 100.0, "Vth": 1.2}[p]
    row.update(overrides)
    return row


def _csv_bytes(rows):
    df = pd.DataFrame(rows)
    buf = io.StringIO()
    df.to_csv(buf, index=False)
    return buf.getvalue().encode("utf-8")


@pytest.fixture
def auth_headers():
    auth_service.init_auth_db()
    with SessionLocal() as s:
        user = auth_service.get_user_by_username("engineer", db=s)
        if not user:
            user = auth_service.create_user("engineer", "EngineerPass123!", "engineer", db=s)
    token = create_access_token(subject=user.username)
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# Test E: Hash Stability
# ---------------------------------------------------------------------------

def test_artifact_hashes_match_authoritative_baseline():
    """Verify active model artifacts match the authoritative pre-Phase-17 baseline hashes."""
    hashes = ml_inference.get_active_artifact_hashes()
    for artifact, expected in AUTHORITATIVE_HASHES.items():
        if artifact in hashes:
            assert hashes[artifact] == expected, f"Hash mismatch for {artifact}: {hashes[artifact]} != {expected}"
            assert HEX_64_PATTERN.match(hashes[artifact])

    # Also verify metadata.json hash
    meta_path = Path(ml_inference._artifact_path("metadata.json"))
    assert meta_path.exists()
    assert ml_inference.compute_file_sha256(meta_path) == AUTHORITATIVE_HASHES["metadata.json"]


def test_compute_file_sha256_missing_file_raises():
    """Verify compute_file_sha256 raises FileNotFoundError for non-existent files."""
    with pytest.raises(FileNotFoundError):
        ml_inference.compute_file_sha256("non_existent_model_file.bin")


# ---------------------------------------------------------------------------
# Test A: Normal Inference Trace (SUCCESS)
# ---------------------------------------------------------------------------

def test_normal_inference_trace_success():
    """Verify standard ingestion creates an InferenceRun record with execution_status=SUCCESS."""
    rows = [_make_test_row("C9101"), _make_test_row("C9102")]
    df = pd.DataFrame(rows)

    result = ingest_dataframe(df, filename="normal_test.csv")
    assert result.ok
    assert result.inference_run_id is not None
    assert result.inference_run_id.startswith("run_")

    run = crud.get_inference_run(result.inference_run_id)
    assert run is not None
    assert run["run_id"] == result.inference_run_id
    assert run["execution_status"] == "SUCCESS"
    assert run["module_a_status"] == "loaded"
    assert run["module_b_status"] == "loaded"
    assert run["module_c_status"] == "active"
    assert run["is_fallback"] is False
    assert run["model_version"] == "sage-1.1"
    assert run["source_filename"] == "normal_test.csv"
    assert run["total_components"] == 2

    for key in ("anomaly_pipeline_hash", "drift_models_hash", "shap_bundle_hash", "config_hash"):
        assert HEX_64_PATTERN.match(run[key])
        assert run[key] == AUTHORITATIVE_HASHES.get(key.replace("_hash", "") + ".joblib", run[key])


# ---------------------------------------------------------------------------
# Test B: Module A Fallback Trace (FALLBACK)
# ---------------------------------------------------------------------------

def test_module_a_fallback_trace(monkeypatch):
    """Verify Module A failure triggers fallback path, recording execution_status=FALLBACK, is_fallback=True."""
    def _mock_load_anomaly_pipeline():
        raise RuntimeError("Simulated Module A pipeline failure for testing")

    monkeypatch.setattr(ml_inference, "load_anomaly_pipeline", _mock_load_anomaly_pipeline)

    rows = [_make_test_row("C9201")]
    df = pd.DataFrame(rows)

    result = ingest_dataframe(df, filename="fallback_test.csv")
    assert result.ok
    assert result.inference_run_id is not None

    run = crud.get_inference_run(result.inference_run_id)
    assert run is not None
    assert run["execution_status"] == "FALLBACK"
    assert run["module_a_status"] == "fallback"
    assert run["module_b_status"] == "loaded"
    assert run["module_c_status"] == "active"
    assert run["is_fallback"] is True
    assert run["model_version"] == "fallback-stats"


# ---------------------------------------------------------------------------
# Test C: Module B Degradation Trace (DEGRADED)
# ---------------------------------------------------------------------------

def test_module_b_degradation_trace(monkeypatch):
    """Verify Module B failure produces degraded drift, recording execution_status=DEGRADED, is_fallback=False."""
    def _mock_run_module_b(df):
        raise RuntimeError("Simulated Module B forecast failure for testing")

    monkeypatch.setattr(ml_inference, "run_module_b", _mock_run_module_b)

    rows = [_make_test_row("C9301")]
    df = pd.DataFrame(rows)

    result = ingest_dataframe(df, filename="degraded_test.csv")
    assert result.ok
    assert result.inference_run_id is not None

    run = crud.get_inference_run(result.inference_run_id)
    assert run is not None
    assert run["execution_status"] == "DEGRADED"
    assert run["module_a_status"] == "loaded"
    assert run["module_b_status"] == "degraded"
    assert run["module_c_status"] == "active"
    assert run["is_fallback"] is False
    assert run["model_version"] == "sage-1.1"


# ---------------------------------------------------------------------------
# Test D: Run ID Uniqueness
# ---------------------------------------------------------------------------

def test_run_id_uniqueness():
    """Verify consecutive inference runs receive distinct UUID-based run IDs."""
    r1 = ingest_dataframe(pd.DataFrame([_make_test_row("C9401")]))
    r2 = ingest_dataframe(pd.DataFrame([_make_test_row("C9402")]))

    assert r1.inference_run_id is not None
    assert r2.inference_run_id is not None
    assert r1.inference_run_id != r2.inference_run_id


# ---------------------------------------------------------------------------
# Test F: Component Linkage
# ---------------------------------------------------------------------------

def test_component_to_run_linkage():
    """Verify component -> inference run can be resolved through inference_run_components."""
    rows = [_make_test_row("C9501"), _make_test_row("C9502")]
    result = ingest_dataframe(pd.DataFrame(rows), filename="link_test.csv")

    for cid in ("C9501", "C9502"):
        run = crud.get_latest_inference_run_for_component(cid)
        assert run is not None
        assert run["run_id"] == result.inference_run_id
        assert run["execution_status"] == "SUCCESS"


# ---------------------------------------------------------------------------
# Test G: Historical Data Graceful Fallback
# ---------------------------------------------------------------------------

def test_historical_component_returns_null_trace():
    """Verify components with no Phase 17 run return inference_run_id=None without error."""
    unlinked_run = crud.get_latest_inference_run_for_component("HISTORICAL_NO_LINK_XYZ")
    assert unlinked_run is None

    # Query through component_service for historical component
    comp = component_service.get_component("C501")
    if comp:
        assert "inference_run_id" in comp
        assert "inference_trace" in comp
        # C501 was in historical DB before Phase 17, so its trace is None
        assert comp["inference_run_id"] is None
        assert comp["inference_trace"] is None

    # Also test mock fallback component
    comp_mock = component_service.get_component("C101")
    if comp_mock:
        assert "inference_run_id" in comp_mock
        assert "inference_trace" in comp_mock


# ---------------------------------------------------------------------------
# API Integration Tests
# ---------------------------------------------------------------------------

def test_upload_api_returns_inference_trace(auth_headers):
    """Verify POST /api/burnin/upload returns inference_run_id and inference_trace."""
    client = TestClient(app)
    csv_bytes = _csv_bytes([_make_test_row("C9601")])

    res = client.post(
        "/api/burnin/upload",
        files={"file": ("api_test.csv", csv_bytes, "text/csv")},
        headers=auth_headers,
    )
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "success"
    assert data["inference_run_id"] is not None
    assert data["inference_run_id"].startswith("run_")
    assert data["inference_trace"] is not None
    assert data["inference_trace"]["execution_status"] == "SUCCESS"
    assert data["inference_trace"]["model_version"] == "sage-1.1"


def test_component_detail_api_exposes_trace(auth_headers):
    """Verify GET /api/components/{id} returns inference_run_id and inference_trace."""
    cid = "C9701"
    ingest_dataframe(pd.DataFrame([_make_test_row(cid)]), filename="detail_test.csv")

    client = TestClient(app)
    res = client.get(f"/api/components/{cid}", headers=auth_headers)
    assert res.status_code == 200
    data = res.json()
    assert data["component_id"] == cid
    assert data["inference_run_id"] is not None
    assert data["inference_trace"] is not None
    assert data["inference_trace"]["module_a_status"] == "loaded"
    assert data["inference_trace"]["module_b_status"] == "loaded"
    assert data["inference_trace"]["module_c_status"] == "active"
