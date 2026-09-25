"""
Automated unit and integration tests for Phase 15 Engineer Review & Disposition Workflow.
SAGE / SIH 2026 — Semiconductor Burn-In & Latent Defect Screening Console
Team: BINARY BADDIES
"""

import json
import pytest
from fastapi.testclient import TestClient

from app.core.security import create_access_token
from app.main import app
from app.services import auth_service, audit_service, review_service


@pytest.fixture(autouse=True)
def setup_review_env():
    """Ensure auth, audit, and review tables exist and mock components exist."""
    auth_service.init_auth_db()
    audit_service.init_audit_db()
    review_service.init_reviews_db()
    from app.services import mock_data
    for cid in ["C501", "C502", "C503", "C504", "C505", "C506", "C507", "C508"]:
        mock_data.MOCK_COMPONENTS.setdefault(cid, {
            "component_id": cid,
            "lot_id": "ISRO-SCL-250113-02",
            "status": "HOLD",
            "risk_level": "MEDIUM",
            "anomaly_score": 21.6,
            "predicted_168h": 22.0,
            "risk_score": 25.0,
            "decision": "HOLD",
            "confidence": 0.85,
        })


@pytest.fixture()
def unauth_client():
    return TestClient(app)


@pytest.fixture()
def engineer_token():
    return create_access_token(subject="engineer")


@pytest.fixture()
def admin_token():
    return create_access_token(subject="admin")


@pytest.fixture()
def reviewer_token():
    return create_access_token(subject="reviewer")


# 1. Engineer can create PASS disposition
def test_engineer_create_pass_disposition(unauth_client: TestClient, engineer_token: str):
    resp = unauth_client.post(
        "/api/components/C501/review",
        json={"disposition": "PASS", "comment": "Thermal screening stable. Cleared for flight batch."},
        headers={"Authorization": f"Bearer {engineer_token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["component_id"] == "C501"
    assert data["disposition"] == "PASS"
    assert data["username"] == "engineer"
    assert data["role"] == "engineer"
    assert data["comment"] == "Thermal screening stable. Cleared for flight batch."


# 2. Engineer can create HOLD disposition
def test_engineer_create_hold_disposition(unauth_client: TestClient, engineer_token: str):
    resp = unauth_client.post(
        "/api/components/C502/review",
        json={"disposition": "HOLD", "comment": "Leakage delta observed; holding for 168h bake re-test."},
        headers={"Authorization": f"Bearer {engineer_token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["component_id"] == "C502"
    assert data["disposition"] == "HOLD"
    assert data["username"] == "engineer"


# 3. Engineer can create QUARANTINE disposition
def test_engineer_create_quarantine_disposition(unauth_client: TestClient, engineer_token: str):
    resp = unauth_client.post(
        "/api/components/C503/review",
        json={"disposition": "QUARANTINE", "comment": "Oxide breakdown signature detected. Quarantined."},
        headers={"Authorization": f"Bearer {engineer_token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["component_id"] == "C503"
    assert data["disposition"] == "QUARANTINE"


# 4. Admin can create disposition
def test_admin_create_disposition(unauth_client: TestClient, admin_token: str):
    resp = unauth_client.post(
        "/api/components/C504/review",
        json={"disposition": "PASS", "comment": "Admin override following secondary wafer-level review."},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["component_id"] == "C504"
    assert data["disposition"] == "PASS"
    assert data["username"] == "admin"
    assert data["role"] == "admin"


# 5. Reviewer cannot create disposition (403 Forbidden)
def test_reviewer_cannot_create_disposition(unauth_client: TestClient, reviewer_token: str):
    resp = unauth_client.post(
        "/api/components/C501/review",
        json={"disposition": "PASS", "comment": "Reviewer attempt."},
        headers={"Authorization": f"Bearer {reviewer_token}"},
    )
    assert resp.status_code == 403
    assert "insufficient role permissions" in resp.json()["detail"].lower()


# 6. Unauthenticated create returns 401
def test_unauthenticated_create_review(unauth_client: TestClient):
    resp = unauth_client.post(
        "/api/components/C501/review",
        json={"disposition": "PASS", "comment": "Anonymous attempt."},
    )
    assert resp.status_code == 401


# 7. Invalid disposition is rejected
def test_invalid_disposition_rejected(unauth_client: TestClient, engineer_token: str):
    resp = unauth_client.post(
        "/api/components/C501/review",
        json={"disposition": "INVALID_STATE", "comment": "Bad state."},
        headers={"Authorization": f"Bearer {engineer_token}"},
    )
    assert resp.status_code in {400, 422}


# 8. Nonexistent component is rejected (404)
def test_nonexistent_component_review_rejected(unauth_client: TestClient, engineer_token: str):
    resp = unauth_client.post(
        "/api/components/NONEXISTENT_PART_9999/review",
        json={"disposition": "PASS", "comment": "Fake component."},
        headers={"Authorization": f"Bearer {engineer_token}"},
    )
    assert resp.status_code == 404
    assert "not found" in resp.json()["detail"].lower()


# 9. Oversized comment is rejected (422)
def test_oversized_comment_rejected(unauth_client: TestClient, engineer_token: str):
    huge_comment = "A" * 1500
    resp = unauth_client.post(
        "/api/components/C501/review",
        json={"disposition": "PASS", "comment": huge_comment},
        headers={"Authorization": f"Bearer {engineer_token}"},
    )
    assert resp.status_code in {400, 422}


# 10. Authenticated user can read review history
def test_authenticated_read_review_history(unauth_client: TestClient, engineer_token: str):
    resp = unauth_client.get(
        "/api/components/C501/reviews",
        headers={"Authorization": f"Bearer {engineer_token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "component_id" in data
    assert "current_disposition" in data
    assert "total_reviews" in data
    assert isinstance(data["reviews"], list)


# 11. Reviewer can read review history
def test_reviewer_can_read_review_history(unauth_client: TestClient, reviewer_token: str):
    resp = unauth_client.get(
        "/api/components/C501/reviews",
        headers={"Authorization": f"Bearer {reviewer_token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["component_id"] == "C501"
    assert isinstance(data["reviews"], list)


# 12. Historical reviews are preserved (append-only)
def test_historical_reviews_preserved(unauth_client: TestClient, engineer_token: str, admin_token: str):
    # Step 1: Engineer sets HOLD
    r1 = unauth_client.post(
        "/api/components/C505/review",
        json={"disposition": "HOLD", "comment": "First review: holding for test."},
        headers={"Authorization": f"Bearer {engineer_token}"},
    )
    assert r1.status_code == 200

    # Step 2: Admin updates to PASS
    r2 = unauth_client.post(
        "/api/components/C505/review",
        json={"disposition": "PASS", "comment": "Second review: re-test cleared."},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert r2.status_code == 200

    # Query history
    hist_resp = unauth_client.get(
        "/api/components/C505/reviews",
        headers={"Authorization": f"Bearer {engineer_token}"},
    )
    assert hist_resp.status_code == 200
    data = hist_resp.json()
    assert data["total_reviews"] >= 2
    assert data["current_disposition"] == "PASS"

    dispositions = [rev["disposition"] for rev in data["reviews"]]
    assert "HOLD" in dispositions
    assert "PASS" in dispositions


# 13. Latest disposition is retrievable
def test_latest_disposition_retrievable(unauth_client: TestClient, engineer_token: str):
    unauth_client.post(
        "/api/components/C506/review",
        json={"disposition": "QUARANTINE", "comment": "Latest disposition check."},
        headers={"Authorization": f"Bearer {engineer_token}"},
    )
    hist_resp = unauth_client.get(
        "/api/components/C506/reviews",
        headers={"Authorization": f"Bearer {engineer_token}"},
    )
    assert hist_resp.status_code == 200
    assert hist_resp.json()["current_disposition"] == "QUARANTINE"


# 14. REVIEW_DISPOSITION success audit is created
def test_review_disposition_audit_created(unauth_client: TestClient, engineer_token: str):
    unauth_client.post(
        "/api/components/C507/review",
        json={"disposition": "PASS", "comment": "Audit test."},
        headers={"Authorization": f"Bearer {engineer_token}"},
    )
    total, logs = audit_service.get_audit_logs(event_type="REVIEW_DISPOSITION", username="engineer")
    assert total >= 1
    latest = logs[0]
    assert latest.event_type == "REVIEW_DISPOSITION"
    assert latest.event_status == "SUCCESS"
    assert latest.target_entity_type == "component"
    assert latest.target_entity_id == "C507"


# 15. Failed reviewer attempt logs PERMISSION_FAILURE audit
def test_failed_reviewer_attempt_audited(unauth_client: TestClient, reviewer_token: str):
    unauth_client.post(
        "/api/components/C501/review",
        json={"disposition": "PASS", "comment": "Should be blocked."},
        headers={"Authorization": f"Bearer {reviewer_token}"},
    )
    total, logs = audit_service.get_audit_logs(event_type="PERMISSION_FAILURE", username="reviewer")
    assert total >= 1
    fail_logs = [l for l in logs if "/review" in l.endpoint]
    assert len(fail_logs) >= 1
    assert fail_logs[0].event_status == "FAILURE"


# 16. Secrets/passwords/JWTs do not appear in review audit details
def test_zero_secrets_in_review_audit_details(unauth_client: TestClient, engineer_token: str):
    unauth_client.post(
        "/api/components/C508/review",
        json={"disposition": "HOLD", "comment": "Checking zero secret leakage."},
        headers={"Authorization": f"Bearer {engineer_token}"},
    )
    total, logs = audit_service.get_audit_logs(event_type="REVIEW_DISPOSITION", username="engineer")
    assert total >= 1
    for log in logs:
        details_text = (log.details or "").lower()
        assert "password" not in details_text
        assert "token" not in details_text
        assert "bearer" not in details_text
        assert engineer_token not in (log.details or "")


# 17. Unauthenticated reviews read returns 401
def test_unauthenticated_read_reviews_rejected(unauth_client: TestClient):
    resp = unauth_client.get("/api/components/C501/reviews")
    assert resp.status_code == 401


# 18. Nonexistent component read reviews returns 404
def test_nonexistent_component_read_reviews_404(unauth_client: TestClient, engineer_token: str):
    resp = unauth_client.get(
        "/api/components/NONEXISTENT_PART_888/reviews",
        headers={"Authorization": f"Bearer {engineer_token}"},
    )
    assert resp.status_code == 404
