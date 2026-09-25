"""
Automated unit and integration tests for Phase 14 Append-Only Audit Trail.
SAGE / SIH 2026 — Semiconductor Burn-In & Latent Defect Screening Console
Team: BINARY BADDIES
"""

import json
import pytest
from fastapi.testclient import TestClient

from app.core.security import create_access_token
from app.main import app
from app.services import auth_service, audit_service
from database import crud


@pytest.fixture(autouse=True)
def setup_audit_env():
    """Ensure auth and audit tables are initialized."""
    auth_service.init_auth_db()
    audit_service.init_audit_db()


@pytest.fixture()
def unauth_client():
    return TestClient(app)


# 1. Login success audit
def test_login_success_audit(unauth_client: TestClient):
    """Valid login creates LOGIN / SUCCESS audit record with user info and no secrets."""
    resp = unauth_client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "sage2026"},
    )
    assert resp.status_code == 200
    token = resp.json()["access_token"]

    total, logs = audit_service.get_audit_logs(event_type="LOGIN", username="admin")
    assert total >= 1
    latest = logs[0]
    assert latest.event_type == "LOGIN"
    assert latest.event_status == "SUCCESS"
    assert latest.username == "admin"
    assert latest.role == "admin"
    assert latest.endpoint == "/api/auth/login"
    assert latest.http_method == "POST"

    # Privacy verification: no password or JWT in audit record
    assert "sage2026" not in (latest.details or "")
    assert token not in (latest.details or "")
    assert "password" not in (latest.details or "").lower()


# 2. Login failure audit (wrong password)
def test_login_failure_wrong_password_audit(unauth_client: TestClient):
    """Failed login with wrong password creates LOGIN / FAILURE audit record without leaking password."""
    resp = unauth_client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "wrong_password_xyz999"},
    )
    assert resp.status_code == 401

    total, logs = audit_service.get_audit_logs(event_type="LOGIN", username="admin")
    failures = [l for l in logs if l.event_status == "FAILURE"]
    assert len(failures) >= 1
    latest = failures[0]
    assert latest.event_status == "FAILURE"
    assert latest.username == "admin"
    assert latest.endpoint == "/api/auth/login"

    # Privacy verification: secret password attempt is NOT stored
    assert "wrong_password_xyz999" not in (latest.details or "")
    assert "password" not in (latest.details or "").lower()


# 3. Login failure audit (unknown user)
def test_login_failure_unknown_user_audit(unauth_client: TestClient):
    """Failed login with unknown user creates LOGIN / FAILURE record without leaking user existence."""
    resp = unauth_client.post(
        "/api/auth/login",
        json={"username": "unknown_intruder_user", "password": "secret_attempt_888"},
    )
    assert resp.status_code == 401

    total, logs = audit_service.get_audit_logs(event_type="LOGIN", username="unknown_intruder_user")
    assert total >= 1
    latest = logs[0]
    assert latest.event_status == "FAILURE"
    assert latest.username == "unknown_intruder_user"
    assert latest.user_id is None

    # Privacy verification
    assert "secret_attempt_888" not in (latest.details or "")
    assert "password" not in (latest.details or "").lower()


# 4. Logout audit
def test_logout_audit(unauth_client: TestClient):
    """Authenticated logout creates LOGOUT / SUCCESS audit record."""
    # Obtain token
    login_resp = unauth_client.post(
        "/api/auth/login",
        json={"username": "engineer", "password": "sage2026"},
    )
    assert login_resp.status_code == 200
    token = login_resp.json()["access_token"]

    # Logout
    logout_resp = unauth_client.post(
        "/api/auth/logout",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert logout_resp.status_code == 200

    total, logs = audit_service.get_audit_logs(event_type="LOGOUT", username="engineer")
    assert total >= 1
    latest = logs[0]
    assert latest.event_type == "LOGOUT"
    assert latest.event_status == "SUCCESS"
    assert latest.username == "engineer"
    assert latest.role == "engineer"
    assert latest.endpoint == "/api/auth/logout"
    assert latest.http_method == "POST"

    # Token must not be stored in audit row
    assert token not in (latest.details or "")


# 5. RBAC permission failure audit (admin-check)
def test_rbac_permission_failure_audit(unauth_client: TestClient):
    """Reviewer calling admin endpoint creates PERMISSION_FAILURE audit record and receives 403."""
    login_resp = unauth_client.post(
        "/api/auth/login",
        json={"username": "reviewer", "password": "sage2026"},
    )
    assert login_resp.status_code == 200
    token = login_resp.json()["access_token"]

    resp = unauth_client.get(
        "/api/auth/admin-check",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403

    total, logs = audit_service.get_audit_logs(event_type="PERMISSION_FAILURE", username="reviewer")
    assert total >= 1
    latest = logs[0]
    assert latest.event_type == "PERMISSION_FAILURE"
    assert latest.event_status == "FAILURE"
    assert latest.username == "reviewer"
    assert latest.role == "reviewer"
    assert latest.endpoint == "/api/auth/admin-check"
    assert latest.http_method == "GET"


# 6. Reviewer denied upload permission failure audit
def test_reviewer_upload_permission_failure_audit(unauth_client: TestClient):
    """Reviewer attempting burn-in upload produces 403 and PERMISSION_FAILURE audit log."""
    login_resp = unauth_client.post(
        "/api/auth/login",
        json={"username": "reviewer", "password": "sage2026"},
    )
    token = login_resp.json()["access_token"]

    resp = unauth_client.post(
        "/api/burnin/upload",
        files={"file": ("test.csv", b"a,b\n1,2\n", "text/csv")},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403

    total, logs = audit_service.get_audit_logs(event_type="PERMISSION_FAILURE", username="reviewer")
    upload_fails = [l for l in logs if "/api/burnin/upload" in l.endpoint]
    assert len(upload_fails) >= 1
    latest = upload_fails[0]
    assert latest.event_type == "PERMISSION_FAILURE"
    assert latest.event_status == "FAILURE"
    assert latest.http_method == "POST"


# 7. Burn-in upload success audit
def test_burnin_upload_success_audit(client: TestClient):
    """Successful burn-in upload creates BURNIN_UPLOAD / SUCCESS audit record with safe metadata."""
    resp = client.post(
        "/api/burnin/upload",
        files={"file": ("audit_test_file.csv", b"a,b\n1,2\n", "text/csv")},
    )
    assert resp.status_code == 200

    total, logs = audit_service.get_audit_logs(event_type="BURNIN_UPLOAD")
    successes = [l for l in logs if l.event_status == "SUCCESS" and "audit_test_file.csv" in (l.details or "")]
    assert len(successes) >= 1
    latest = successes[0]
    assert latest.event_type == "BURNIN_UPLOAD"
    assert latest.event_status == "SUCCESS"
    assert latest.target_entity_type == "file"
    assert latest.target_entity_id == "audit_test_file.csv"

    # Details has metadata, no raw contents
    details_data = json.loads(latest.details)
    assert details_data["filename"] == "audit_test_file.csv"
    assert "raw_content" not in details_data


# 8. Burn-in upload failure audit (wrong file extension)
def test_burnin_upload_failure_audit(client: TestClient):
    """Invalid upload produces 400 and BURNIN_UPLOAD / FAILURE audit record."""
    resp = client.post(
        "/api/burnin/upload",
        files={"file": ("invalid_report.txt", b"some text", "text/plain")},
    )
    assert resp.status_code == 400

    total, logs = audit_service.get_audit_logs(event_type="BURNIN_UPLOAD")
    failures = [l for l in logs if l.event_status == "FAILURE" and "invalid_report.txt" in (l.details or "")]
    assert len(failures) >= 1
    latest = failures[0]
    assert latest.event_type == "BURNIN_UPLOAD"
    assert latest.event_status == "FAILURE"


# 9. Administrative read-only audit log endpoint
def test_admin_read_audit_logs(client: TestClient, unauth_client: TestClient):
    """Admin can query /api/system/audit-logs; reviewer and engineer receive 403."""
    # Admin access via default client fixture
    admin_resp = client.get("/api/system/audit-logs")
    assert admin_resp.status_code == 200
    data = admin_resp.json()
    assert "total" in data
    assert "items" in data
    assert isinstance(data["items"], list)

    # Reviewer access
    rev_token = create_access_token(subject="reviewer")
    rev_resp = unauth_client.get(
        "/api/system/audit-logs",
        headers={"Authorization": f"Bearer {rev_token}"},
    )
    assert rev_resp.status_code == 403

    # Engineer access
    eng_token = create_access_token(subject="engineer")
    eng_resp = unauth_client.get(
        "/api/system/audit-logs",
        headers={"Authorization": f"Bearer {eng_token}"},
    )
    assert eng_resp.status_code == 403


# 10. Append-only requirement: no mutation endpoints
def test_audit_append_only_no_mutation(client: TestClient):
    """Audit logs cannot be modified or deleted via API endpoints."""
    # Attempt PUT
    put_resp = client.put("/api/system/audit-logs", json={"some": "data"})
    assert put_resp.status_code == 405

    # Attempt POST
    post_resp = client.post("/api/system/audit-logs", json={"some": "data"})
    assert post_resp.status_code == 405

    # Attempt DELETE
    del_resp = client.delete("/api/system/audit-logs/1")
    assert del_resp.status_code in {404, 405}


# 11. Privacy sanitizer unit test
def test_audit_privacy_sanitizer():
    """Sanitizer aggressively scrubs secrets, tokens, and prohibited fields."""
    dirty_details = {
        "password": "SuperSecretPassword123!",
        "hashed_password": "$2b$12$abcdefg",
        "token": "secret_token_val",
        "access_token": "secret_jwt",
        "Authorization": "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.xyz",
        "cookie": "session_id=12345",
        "filename": "test.csv",
        "nested": {
            "password": "leak",
            "safe_counter": 42,
        },
    }
    sanitized_str = audit_service.sanitize_details(dirty_details)
    sanitized = json.loads(sanitized_str)

    assert "password" not in sanitized
    assert "hashed_password" not in sanitized
    assert "token" not in sanitized
    assert "access_token" not in sanitized
    assert "Authorization" not in sanitized
    assert "cookie" not in sanitized
    assert "password" not in sanitized["nested"]
    assert sanitized["nested"]["safe_counter"] == 42
    assert sanitized["filename"] == "test.csv"
