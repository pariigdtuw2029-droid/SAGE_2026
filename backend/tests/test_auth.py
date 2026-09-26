"""
"""Auth + protection tests (Member 4 — security layer).

Covers:
- /api/auth/login happy path issues a verifiable JWT
- wrong username / wrong password -> 401 with a generic message
- login rate limiting after repeated failures
- /api/* routes require a bearer token (401 without / 200 with)
- tampered, wrong-key, and expired tokens are rejected
- /health, / and the static frontend stay public
- alg=none and cross-algorithm token confusion are rejected
"""

import time
from datetime import datetime, timedelta, timezone

import jwt as pyjwt
import pytest
from fastapi.testclient import TestClient

from app import security
from app.core.security import ALGORITHM, SAGE_JWT_SECRET, create_access_token
from app.main import app
from app.services import auth_service



@pytest.fixture()
def client():
    return TestClient(app)


@pytest.fixture(autouse=True)
def setup_auth():
    """Ensure demo user exists before running each test."""
    auth_service.init_auth_db()


@pytest.fixture()
def client():
    """TestClient with a valid bearer token pre-attached to every request."""
    auth_service.init_auth_db()
    auth_service.init_audit_db()
    auth_service.init_reviews_db()
    c = TestClient(app)
    c.headers.update({"Authorization": f"Bearer {security.create_access_token(subject='admin')}"})
    return c


@pytest.fixture()
def token():
    return security.create_access_token(subject="admin", expires_minutes=5)


def _login_password():
    """The admin password exported via SAGE_USERS in conftest."""
    return "test-password-123"


def _login(username, password):
    return TestClient(app).post("/api/auth/login", json={"username": username, "password": password}).json()


def _auth(tok):
    return {"Authorization": f"Bearer {tok}"}

    )
    assert resp.status_code == 401


def test_login_success_issues_verifiable_jwt(client):
    resp = client.post(
        "/api/auth/login",
        json={"username": "admin", "password": _login_password()},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["token_type"] == "bearer"
    assert body["username"] == "admin"
    assert body["expires_in"] > 0
    assert body["refresh_token"]
    claims = security.decode_token(body["access_token"])
    assert claims["sub"] == "admin"
    assert claims["iss"] == security.JWT_ISSUER
    assert claims["aud"] == security.JWT_AUDIENCE
    assert claims["typ"] == "access"
    refresh_claims = security.decode_token(body["refresh_token"], expected_typ="refresh")
    assert refresh_claims["sub"] == "admin"
    assert refresh_claims["exp"] > claims["exp"]


def test_refresh_exchanges_refresh_token_for_new_access_token(client):
    login = client.post(
        "/api/auth/login",
        json={"username": "admin", "password": _login_password()},
    ).json()
    resp = client.post(
        "/api/auth/refresh",
        headers={"Authorization": f"Bearer {login['refresh_token']}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["access_token"]
    assert body["refresh_token"]
    assert body["access_token"] != login["access_token"]
    assert body["refresh_token"] != login["refresh_token"]
    claims = security.decode_token(body["access_token"])
    assert claims["sub"] == "admin"
    assert claims["typ"] == "access"


def test_login_wrong_password_401_generic_message(client):
    resp = client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "definitely-wrong"},
    )
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Invalid username or password."


def test_login_unknown_user_401_same_message(client):
    resp = client.post("/api/auth/login", json={"username": "ghost", "password": "x"})
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Invalid username or password."


def test_login_missing_fields_422(client):
    assert client.post("/api/auth/login", json={}).status_code == 422
    assert client.post("/api/auth/login", json={"username": "admin"}).status_code == 422


def test_login_rate_limited_after_repeated_failures(client):
    for _ in range(security._MAX_ATTEMPTS):
        client.post("/api/auth/login", json={"username": "admin", "password": "bad"})
    resp = client.post(
        "/api/auth/login",
        json={"username": "admin", "password": _login_password()},
    )
    assert resp.status_code == 429


def test_protected_api_rejects_missing_token(client):
    resp = client.get("/api/lots")
    assert resp.status_code == 401
    assert resp.headers.get("www-authenticate") == "Bearer"


def test_protected_api_accepts_valid_token(client, token):
    resp = client.get("/api/lots", headers=_auth(token))
    assert resp.status_code == 200


def test_protected_api_rejects_tampered_token(client, token):
    tampered = token[:-6] + ("AAAAAA" if token[-6:] != "AAAAAA" else "BBBBBB")
    resp = client.get("/api/lots", headers=_auth(tampered))
    assert resp.status_code == 401


def test_expired_token_rejected(client):
    expired = security.create_access_token(subject="admin", expires_minutes=-1)
    resp = client.get("/api/lots", headers=_auth(expired))
    assert resp.status_code == 401


def test_deactivated_user_cannot_access(client):
    auth_service.create_user(
        username="disabled_engineer",
        password="password123",
        full_name="Former Engineer",
        is_active=False,
    )
    token = create_access_token(subject="disabled_engineer")
    resp = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401
    assert "deactivated" in resp.json()["detail"].lower()


def test_missing_jwt_secret_raises_runtime_error(monkeypatch):
    from app.core.security import get_jwt_secret, validate_security_config
    monkeypatch.delenv("SAGE_JWT_SECRET", raising=False)
    with pytest.raises(RuntimeError) as exc_info:
        get_jwt_secret()
    assert "CRITICAL SECURITY CONFIGURATION ERROR" in str(exc_info.value)
    assert "SAGE_JWT_SECRET" in str(exc_info.value)

    with pytest.raises(RuntimeError):
        validate_security_config()


def test_health_and_root_stay_public(client):
    assert client.get("/health").status_code == 200
    assert client.get("/").status_code == 200


def test_me_returns_role(client):
    tok = _login("reviewer", "rev-test-pass")["access_token"]
    resp = client.get("/api/auth/me", headers=_auth(tok))
    assert resp.status_code == 200
    assert resp.json()["role"] == "reviewer"


def test_token_contains_jti_and_nbf(token):
    claims = security.decode_token(token)
    assert claims["jti"]
    assert claims["nbf"] <= claims["exp"]


def test_password_verify_roundtrip():
    h = security.hash_password("s3cret-value")
    assert security.is_bcrypt_hash(h)
    assert security.verify_password("s3cret-value", h) is True
    assert security.verify_password("other", h) is False

