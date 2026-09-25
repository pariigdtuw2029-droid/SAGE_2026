"""
Automated unit and integration tests for Phase 16 Data Quality and Validation Gate.
SAGE / SIH 2026 -- Semiconductor Burn-In and Latent Defect Screening Console
Team: BINARY BADDIES
"""

import io
import json
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app.core.security import create_access_token
from app.main import app
from app.services import audit_service, auth_service
from database.validation import (
    validate_dataframe,
    filter_valid_rows,
    DataValidationError,
    CATEGORY_MISSING_REQUIRED_FIELD,
    CATEGORY_INVALID_TYPE,
    CATEGORY_INVALID_TIMESTAMP,
    CATEGORY_INVALID_IDENTIFIER,
    CATEGORY_INVALID_NUMERIC_VALUE,
    CATEGORY_DUPLICATE_RECORD,
    CATEGORY_INVALID_RANGE,
    CATEGORY_UNIT_INCONSISTENCY,
)


def _valid_row(component_id="C101", batch="ISRO-SCL-250106-01", part="MOSFET", **overrides):
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


def _df(rows):
    return pd.DataFrame(rows)


def _to_csv_bytes(df):
    buf = io.StringIO()
    df.to_csv(buf, index=False)
    return buf.getvalue().encode("utf-8")


@pytest.fixture(autouse=True)
def setup_env():
    auth_service.init_auth_db()
    audit_service.init_audit_db()


@pytest.fixture()
def unauth_client():
    return TestClient(app)


@pytest.fixture()
def admin_token():
    return create_access_token(subject="admin")


@pytest.fixture()
def engineer_token():
    return create_access_token(subject="engineer")


@pytest.fixture()
def reviewer_token():
    return create_access_token(subject="reviewer")


# 1. Valid upload passes validation (status == PASS)
def test_valid_data_passes_validation():
    df = _df([_valid_row("C101"), _valid_row("C102")])
    report = validate_dataframe(df)
    dq = report.data_quality_summary()
    assert dq["status"] == "PASS"
    assert dq["total_rows"] == 2
    assert dq["accepted_rows"] == 2
    assert dq["rejected_rows"] == 0
    assert dq["warning_rows"] == 0
    assert len(dq["issues"]) == 0


# 2. Missing required field is detected
def test_missing_required_column_detected():
    df = _df([_valid_row("C101")]).drop(columns=["Leakage_0h"])
    report = validate_dataframe(df)
    dq = report.data_quality_summary()
    assert dq["status"] == "REJECTED"
    assert any(i["category"] == CATEGORY_MISSING_REQUIRED_FIELD for i in dq["issues"])


# 3. Missing row-level required Part_Type is detected
def test_missing_part_type_detected():
    df = _df([_valid_row("C101", Part_Type=None)])
    report = validate_dataframe(df)
    dq = report.data_quality_summary()
    assert dq["status"] == "REJECTED"
    assert dq["rejected_rows"] == 1
    assert any(i["category"] == CATEGORY_MISSING_REQUIRED_FIELD and i["field"] == "Part_Type" for i in dq["issues"])


# 4. Invalid identifier detected
def test_invalid_identifier_detected():
    df = _df([_valid_row(component_id="INVALID_ID_999")])
    report = validate_dataframe(df)
    dq = report.data_quality_summary()
    assert dq["rejected_rows"] == 1
    assert any(i["category"] == CATEGORY_INVALID_IDENTIFIER for i in dq["issues"])


# 5. Invalid lot identifier detected
def test_invalid_lot_identifier_detected():
    df = _df([_valid_row(batch="")])
    report = validate_dataframe(df)
    dq = report.data_quality_summary()
    assert dq["rejected_rows"] == 1
    assert any(i["category"] == CATEGORY_INVALID_IDENTIFIER and i["field"] == "Batch_ID" for i in dq["issues"])


# 6. Duplicate record behavior handled correctly
def test_duplicate_record_detected():
    df = _df([_valid_row("C101"), _valid_row("C101")])
    report = validate_dataframe(df)
    dq = report.data_quality_summary()
    assert dq["total_rows"] == 2
    assert dq["accepted_rows"] == 1
    assert dq["rejected_rows"] == 1
    assert any(i["category"] == CATEGORY_DUPLICATE_RECORD for i in dq["issues"])


# 7. Invalid non-numeric value detected
def test_invalid_numeric_value_detected():
    df = _df([_valid_row(**{"Leakage_24h": "not_a_number"})])
    report = validate_dataframe(df)
    dq = report.data_quality_summary()
    assert dq["rejected_rows"] == 1
    assert any(i["category"] == CATEGORY_INVALID_NUMERIC_VALUE for i in dq["issues"])


# 8. Unparseable timestamp detected
def test_unparseable_timestamp_detected():
    df = _df([_valid_row(**{"Timestamp_24h": "bad-date-format"})])
    report = validate_dataframe(df)
    dq = report.data_quality_summary()
    assert dq["rejected_rows"] == 1
    assert any(i["category"] == CATEGORY_INVALID_TIMESTAMP for i in dq["issues"])


# 9. Implausible physical range rejected (INVALID_RANGE)
def test_implausible_range_rejected():
    df = _df([_valid_row(**{"Leakage_24h": 999.0})])
    report = validate_dataframe(df)
    dq = report.data_quality_summary()
    assert dq["rejected_rows"] == 1
    assert any(i["category"] == CATEGORY_INVALID_RANGE for i in dq["issues"])


# 10. Sensor fault sentinel (-1.0) rejected
def test_sensor_fault_sentinel_rejected():
    df = _df([_valid_row(**{"Resistance_24h": -1.0})])
    report = validate_dataframe(df)
    dq = report.data_quality_summary()
    assert dq["rejected_rows"] == 1
    assert any(i["category"] == CATEGORY_INVALID_RANGE for i in dq["issues"])


# 11. Unusual but valid engineering value (Leakage = 45 uA) is NOT rejected
def test_unusual_valid_engineering_value_accepted():
    # 45 uA is unusually high (normal 1.0), but physically plausible (0 to 50.0).
    # Must NOT be classified as bad data; must pass to ML anomaly pipeline.
    df = _df([_valid_row(**{"Leakage_24h": 45.0})])
    report = validate_dataframe(df)
    dq = report.data_quality_summary()
    assert dq["status"] == "PASS"
    assert dq["accepted_rows"] == 1
    assert dq["rejected_rows"] == 0


# 12. Imputable missing reading flagged as WARNING, not rejected
def test_missing_reading_flagged_as_warning():
    df = _df([_valid_row(**{"Resistance_96h": None})])
    report = validate_dataframe(df)
    dq = report.data_quality_summary()
    assert dq["status"] == "WARNING"
    assert dq["accepted_rows"] == 1
    assert dq["rejected_rows"] == 0
    assert dq["warning_rows"] == 1
    assert any(i["severity"] == "WARNING" for i in dq["issues"])


# 13. Unit consistency check flags unrecognized units
def test_unrecognized_unit_flagged_as_warning():
    df = _df([_valid_row(**{"Leakage_Unit": "invalid_unit_xyz"})])
    report = validate_dataframe(df)
    dq = report.data_quality_summary()
    assert dq["status"] == "WARNING"
    assert any(i["category"] == CATEGORY_UNIT_INCONSISTENCY for i in dq["issues"])


# 14. API upload endpoint returns data_quality contract
def test_upload_api_returns_data_quality_contract(unauth_client: TestClient, admin_token: str):
    df = _df([_valid_row("C101"), _valid_row("C102")])
    csv_bytes = _to_csv_bytes(df)

    resp = unauth_client.post(
        "/api/burnin/upload",
        files={"file": ("test_dq.csv", csv_bytes, "text/csv")},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "data_quality" in data
    dq = data["data_quality"]
    assert dq is not None
    assert dq["status"] == "PASS"
    assert dq["total_rows"] == 2
    assert dq["accepted_rows"] == 2
    assert dq["rejected_rows"] == 0


# 15. Reviewer upload is rejected with 403 Forbidden
def test_reviewer_upload_forbidden(unauth_client: TestClient, reviewer_token: str):
    df = _df([_valid_row("C101")])
    resp = unauth_client.post(
        "/api/burnin/upload",
        files={"file": ("test.csv", _to_csv_bytes(df), "text/csv")},
        headers={"Authorization": f"Bearer {reviewer_token}"},
    )
    assert resp.status_code == 403
    assert "insufficient role permissions" in resp.json()["detail"].lower()


# 16. Unauthenticated upload is rejected with 401 Unauthorized
def test_unauthenticated_upload_rejected(unauth_client: TestClient):
    df = _df([_valid_row("C101")])
    resp = unauth_client.post(
        "/api/burnin/upload",
        files={"file": ("test.csv", _to_csv_bytes(df), "text/csv")},
    )
    assert resp.status_code == 401


# 17. Upload audit event logs safe data quality metrics without secrets
def test_upload_audit_logs_safe_data_quality(unauth_client: TestClient, engineer_token: str):
    df = _df([_valid_row("C101")])
    resp = unauth_client.post(
        "/api/burnin/upload",
        files={"file": ("audit_check.csv", _to_csv_bytes(df), "text/csv")},
        headers={"Authorization": f"Bearer {engineer_token}"},
    )
    assert resp.status_code == 200

    total, logs = audit_service.get_audit_logs(event_type="BURNIN_UPLOAD", username="engineer")
    assert total >= 1
    latest = logs[0]
    assert latest.event_status == "SUCCESS"
    assert latest.details is not None
    raw_det = latest.details
    assert "data_quality_status" in raw_det
    assert "total_rows" in raw_det
    assert "valid_rows" in raw_det
    det_dict = json.loads(raw_det) if isinstance(raw_det, str) else raw_det
    # Ensure zero secrets or passwords in audit log
    for k, v in det_dict.items():
        assert "password" not in str(v).lower()
        assert "token" not in str(v).lower()
        assert "bearer" not in str(v).lower()
