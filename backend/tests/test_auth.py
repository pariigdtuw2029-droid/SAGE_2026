"""
Automated unit and integration tests for Phase 12 JWT Authentication.
SAGE / SIH 2026 — Semiconductor Burn-In & Latent Defect Screening Console
Team: BINARY BADDIES
"""

from datetime import datetime, timedelta, timezone

import jwt
import pytest
from fastapi.testclient import TestClient

from app.core.security import ALGORITHM, SAGE_JWT_SECRET, create_access_token
from app.main import app
from app.services import auth_service


@pytest.fixture(autouse=True)
def setup_auth():
    """Ensure demo user exists before running each test."""
    auth_service.init_auth_db()


@pytest.fixture()
def client():
    return TestClient(app)


# Test 1 — Login success
def test_login_success(client: TestClient):
    """Valid credentials produce 200, access_token, token_type=bearer."""
    resp = client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "sage2026"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "access_token" in data
    assert data["token_type"].lower() == "bearer"
    assert "expires_in" in data
    assert data["expires_in"] > 0

    # Verify token payload
    payload = jwt.decode(
        data["access_token"],
        SAGE_JWT_SECRET,
        algorithms=[ALGORITHM],
        options={"require": ["sub", "iat", "exp"]},
    )
    assert payload["sub"] == "admin"


# Test 2 — Wrong password
def test_login_wrong_password(client: TestClient):
    """Wrong password produces 401 with generic message."""
    resp = client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "wrong_password_123"},
    )
    assert resp.status_code == 401
    assert "Invalid username or password" in resp.json()["detail"]


# Test 3 — Unknown user
def test_login_unknown_user(client: TestClient):
    """Non-existent user produces 401 with generic message."""
    resp = client.post(
        "/api/auth/login",
        json={"username": "non_existent_user_999", "password": "password"},
    )
    assert resp.status_code == 401
    assert "Invalid username or password" in resp.json()["detail"]


# Test 4 — Missing credentials
def test_login_missing_credentials(client: TestClient):
    """Missing fields in JSON body produce validation failure (422)."""
    resp = client.post("/api/auth/login", json={"username": "admin"})
    assert resp.status_code == 422

    resp2 = client.post("/api/auth/login", json={"password": "password"})
    assert resp2.status_code == 422

    resp3 = client.post("/api/auth/login", json={})
    assert resp3.status_code == 422


# Test 5 — Invalid JWT
def test_protected_endpoint_invalid_jwt(client: TestClient):
    """Corrupted/invalid signature token produces 401."""
    resp = client.get(
        "/api/auth/me",
        headers={"Authorization": "Bearer invalid.token.value"},
    )
    assert resp.status_code == 401
    assert "Invalid access token" in resp.json()["detail"]


# Test 6 — Expired JWT
def test_protected_endpoint_expired_jwt(client: TestClient):
    """Expired access token produces 401."""
    now = datetime.now(timezone.utc)
    expired_payload = {
        "sub": "admin",
        "iat": int((now - timedelta(hours=2)).timestamp()),
        "exp": int((now - timedelta(hours=1)).timestamp()),
    }
    expired_token = jwt.encode(expired_payload, SAGE_JWT_SECRET, algorithm=ALGORITHM)

    resp = client.get(
        "/api/auth/me",
        headers={"Authorization": f"Bearer {expired_token}"},
    )
    assert resp.status_code == 401
    assert "Access token has expired" in resp.json()["detail"]


# Test 7 — Protected endpoint without token
def test_protected_endpoint_without_token(client: TestClient):
    """Calling protected endpoint without Authorization header produces 401."""
    resp = client.get("/api/auth/me")
    assert resp.status_code == 401
    assert "Authentication token required" in resp.json()["detail"]


# Test 8 — Protected endpoint with valid token
def test_protected_endpoint_with_valid_token(client: TestClient):
    """Calling protected endpoint with valid Bearer token returns 200 + user profile."""
    # Obtain token via login
    login_resp = client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "sage2026"},
    )
    assert login_resp.status_code == 200
    token = login_resp.json()["access_token"]

    # Access protected /api/auth/me
    resp = client.get(
        "/api/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["username"] == "admin"
    assert data["is_active"] is True
    assert data.get("full_name") == "SAGE Test Engineer"


# Test 9 — Tampered JWT signature
def test_protected_endpoint_tampered_signature(client: TestClient):
    """Token signed with wrong key produces 401."""
    now = datetime.now(timezone.utc)
    tampered_payload = {
        "sub": "admin",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(hours=1)).timestamp()),
    }
    tampered_token = jwt.encode(tampered_payload, "completely-wrong-signing-secret-key-32b", algorithm=ALGORITHM)

    resp = client.get(
        "/api/auth/me",
        headers={"Authorization": f"Bearer {tampered_token}"},
    )
    assert resp.status_code == 401


# Test 10 — Deactivated user account
def test_deactivated_user_cannot_access(client: TestClient):
    """Deactivated user account token is rejected with 401."""
    auth_service.create_user(
        username="disabled_engineer",
        password="password123",
        full_name="Former Engineer",
        is_active=False,
    )
    token = create_access_token(subject="disabled_engineer")
    resp = client.get(
        "/api/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 401
    assert "deactivated" in resp.json()["detail"].lower()


# Test 11 — Missing SAGE_JWT_SECRET configuration error
def test_missing_jwt_secret_raises_runtime_error(monkeypatch):
    """Confirm application strictly requires SAGE_JWT_SECRET without fallback."""
    from app.core.security import get_jwt_secret, validate_security_config
    monkeypatch.delenv("SAGE_JWT_SECRET", raising=False)
    with pytest.raises(RuntimeError) as exc_info:
        get_jwt_secret()
    assert "CRITICAL SECURITY CONFIGURATION ERROR" in str(exc_info.value)
    assert "SAGE_JWT_SECRET" in str(exc_info.value)

    with pytest.raises(RuntimeError):
        validate_security_config()

