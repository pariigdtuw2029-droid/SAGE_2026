"""
Automated unit and integration tests for Phase 13 RBAC Foundation & Authenticated Boundary.
SAGE / SIH 2026 — Semiconductor Burn-In & Latent Defect Screening Console
Team: BINARY BADDIES
"""

import pytest
from fastapi.testclient import TestClient

from app.core.security import create_access_token
from app.main import app
from app.models.user import VALID_ROLES
from app.services import auth_service


@pytest.fixture(autouse=True)
def setup_rbac_users():
    """Seed test users with different roles for RBAC verification."""
    auth_service.init_auth_db()

    # Engineer user
    if not auth_service.get_user_by_username("test_engineer"):
        auth_service.create_user(
            username="test_engineer",
            password="password123",
            full_name="Lead Screening Engineer",
            role="engineer",
            is_active=True,
        )

    # Reviewer user (read-only screening audit)
    if not auth_service.get_user_by_username("test_reviewer"):
        auth_service.create_user(
            username="test_reviewer",
            password="password123",
            full_name="QA Compliance Reviewer",
            role="reviewer",
            is_active=True,
        )

    # Inactive engineer
    if not auth_service.get_user_by_username("test_inactive"):
        auth_service.create_user(
            username="test_inactive",
            password="password123",
            full_name="Deactivated Engineer",
            role="engineer",
            is_active=False,
        )


@pytest.fixture()
def unauthed_client():
    """Test client without default authentication headers."""
    return TestClient(app)


# 1. Unauthenticated request to protected endpoint produces 401
def test_protected_lots_unauthenticated(unauthed_client: TestClient):
    resp = unauthed_client.get("/api/lots")
    assert resp.status_code == 401
    assert "token required" in resp.json()["detail"].lower()


# 2. Malformed token produces 401
def test_protected_endpoint_malformed_token(unauthed_client: TestClient):
    resp = unauthed_client.get("/api/lots", headers={"Authorization": "Bearer malformed.jwt.token"})
    assert resp.status_code == 401
    assert "invalid access token" in resp.json()["detail"].lower()


# 3. Inactive user produces 401
def test_inactive_user_rejected(unauthed_client: TestClient):
    token = create_access_token(subject="test_inactive")
    resp = unauthed_client.get("/api/lots", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401
    assert "deactivated" in resp.json()["detail"].lower()


# 4. Valid engineer accesses permitted read endpoints
def test_engineer_permitted_read_access(unauthed_client: TestClient):
    token = create_access_token(subject="test_engineer")
    headers = {"Authorization": f"Bearer {token}"}

    # Lots endpoint
    resp = unauthed_client.get("/api/lots", headers=headers)
    assert resp.status_code == 200

    # User profile contains role
    me_resp = unauthed_client.get("/api/auth/me", headers=headers)
    assert me_resp.status_code == 200
    assert me_resp.json()["role"] == "engineer"
    assert me_resp.json()["username"] == "test_engineer"


# 5. Valid reviewer accesses permitted read endpoints
def test_reviewer_permitted_read_access(unauthed_client: TestClient):
    token = create_access_token(subject="test_reviewer")
    headers = {"Authorization": f"Bearer {token}"}

    resp = unauthed_client.get("/api/lots", headers=headers)
    assert resp.status_code == 200

    me_resp = unauthed_client.get("/api/auth/me", headers=headers)
    assert me_resp.status_code == 200
    assert me_resp.json()["role"] == "reviewer"


# 6. Role restriction: reviewer attempting upload receives 403 Forbidden
def test_reviewer_denied_upload_mutating_action(unauthed_client: TestClient):
    token = create_access_token(subject="test_reviewer")
    headers = {"Authorization": f"Bearer {token}"}

    resp = unauthed_client.post(
        "/api/burnin/upload",
        files={"file": ("test.csv", b"a,b\n1,2\n", "text/csv")},
        headers=headers,
    )
    assert resp.status_code == 403
    assert "insufficient role permissions" in resp.json()["detail"].lower()


# 7. Role restriction: engineer permitted to upload
def test_engineer_permitted_upload(unauthed_client: TestClient):
    token = create_access_token(subject="test_engineer")
    headers = {"Authorization": f"Bearer {token}"}

    resp = unauthed_client.post(
        "/api/burnin/upload",
        files={"file": ("test.csv", b"a,b\n1,2\n", "text/csv")},
        headers=headers,
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "success"


# 8. Admin boundary: admin permitted on admin-check
def test_admin_permitted_on_admin_check(unauthed_client: TestClient):
    token = create_access_token(subject="admin")
    headers = {"Authorization": f"Bearer {token}"}

    resp = unauthed_client.get("/api/auth/admin-check", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["role"] == "admin"


# 9. Admin boundary: engineer rejected with 403 Forbidden on admin-check
def test_engineer_denied_on_admin_check(unauthed_client: TestClient):
    token = create_access_token(subject="test_engineer")
    headers = {"Authorization": f"Bearer {token}"}

    resp = unauthed_client.get("/api/auth/admin-check", headers=headers)
    assert resp.status_code == 403
    assert "insufficient role permissions" in resp.json()["detail"].lower()


# 10. Admin boundary: reviewer rejected with 403 Forbidden on admin-check
def test_reviewer_denied_on_admin_check(unauthed_client: TestClient):
    token = create_access_token(subject="test_reviewer")
    headers = {"Authorization": f"Bearer {token}"}

    resp = unauthed_client.get("/api/auth/admin-check", headers=headers)
    assert resp.status_code == 403
    assert "insufficient role permissions" in resp.json()["detail"].lower()


# 11. Unsupported role rejected in create_user validation
def test_create_user_unsupported_role_rejected():
    with pytest.raises(ValueError) as exc:
        auth_service.create_user(
            username="super_hacker",
            password="password",
            role="superuser_god_mode",
        )
    assert "invalid role" in str(exc.value).lower()


# 12. Tampered / invalid role in user record rejected with 403 Forbidden
def test_user_with_invalid_role_rejected_at_authorization(unauthed_client: TestClient):
    from database.connection import SessionLocal
    from app.models.user import User
    from sqlalchemy import select

    # Directly create user with corrupt role bypassing create_user validation
    with SessionLocal() as session:
        corrupt_user = session.scalar(select(User).where(User.username == "corrupt_role_user"))
        if not corrupt_user:
            corrupt_user = User(
                username="corrupt_role_user",
                hashed_password=auth_service.hash_password("pw123"),
                full_name="Corrupt Role",
                role="unknown_bypassed_role",
                is_active=True,
            )
            session.add(corrupt_user)
            session.commit()

    token = create_access_token(subject="corrupt_role_user")
    resp = unauthed_client.get("/api/lots", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 403
    assert "invalid or unassigned" in resp.json()["detail"].lower()


# ==============================================================================
# Phase 20: Component Report Security & Authenticated Boundary Verification
# ==============================================================================

# Test A — unauthenticated access to report endpoint returns 401
def test_component_report_unauthenticated_denied(unauthed_client: TestClient):
    resp = unauthed_client.get("/api/components/C104/report")
    assert resp.status_code == 401
    assert "token required" in resp.json()["detail"].lower()


# Test B — authenticated engineer allowed (200 OK with valid report payload)
def test_component_report_authenticated_engineer_allowed(unauthed_client: TestClient):
    token = create_access_token(subject="test_engineer")
    resp = unauthed_client.get("/api/components/C104/report", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["component_id"] == "C104"
    assert "decision" in data
    assert "risk_score" in data


# Test C — authenticated reviewer allowed (200 OK for read-only report)
def test_component_report_authenticated_reviewer_allowed(unauthed_client: TestClient):
    token = create_access_token(subject="test_reviewer")
    resp = unauthed_client.get("/api/components/C104/report", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["component_id"] == "C104"
    assert "decision" in data


# Test D — authenticated admin allowed (200 OK)
def test_component_report_authenticated_admin_allowed(unauthed_client: TestClient):
    token = create_access_token(subject="admin")
    resp = unauthed_client.get("/api/components/C104/report", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["component_id"] == "C104"


# Test E — invalid/expired token rejected with 401 Unauthorized
def test_component_report_invalid_token_rejected(unauthed_client: TestClient):
    resp = unauthed_client.get(
        "/api/components/C104/report",
        headers={"Authorization": "Bearer invalid.expired.token"},
    )
    assert resp.status_code == 401
    assert "invalid access token" in resp.json()["detail"].lower()


# Test F — report content preservation for known component (C504)
def test_component_report_preservation_c504(unauthed_client: TestClient):
    import os
    from database.connection import DEFAULT_SQLITE_URL
    from database import crud
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    db_path = DEFAULT_SQLITE_URL.replace("sqlite:///", "")
    if not os.path.exists(db_path):
        return

    engine = create_engine(DEFAULT_SQLITE_URL)
    Session = sessionmaker(bind=engine)
    with Session() as s:
        rep = crud.get_component_report("C504", db=s)
        assert rep is not None
        assert "decision" in rep
        assert "risk_score" in rep
        assert rep["decision"] in ("PASS", "HOLD", "QUARANTINE")

    # Also verify via API schema response
    token = create_access_token(subject="test_engineer")
    resp = unauthed_client.get("/api/components/C104/report", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    data = resp.json()
    assert "current_disposition" in data
    assert "total_reviews" in data
    assert "reviews" in data
    assert "inference_trace" in data
    assert "decision" in data
    assert "risk_score" in data


# Test G — historical component (C501)
def test_component_report_historical_c501():
    import os
    from database.connection import DEFAULT_SQLITE_URL
    from database import crud
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    db_path = DEFAULT_SQLITE_URL.replace("sqlite:///", "")
    if not os.path.exists(db_path):
        return

    engine = create_engine(DEFAULT_SQLITE_URL)
    Session = sessionmaker(bind=engine)
    with Session() as s:
        rep = crud.get_component_report("C501", db=s)
        assert rep is not None
        assert rep["decision"] in ("PASS", "HOLD", "QUARANTINE")
        run = crud.get_latest_inference_run_for_component("C501", db=s)
        assert run is None or "run_id" in run
