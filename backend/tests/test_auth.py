"""
Auth + protection tests (Member 4 — security layer).

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

import jwt as pyjwt
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app import security


@pytest.fixture()
def client():
    return TestClient(app)


@pytest.fixture()
def token():
    return security.create_access_token(subject="admin", expires_minutes=5)


def _login_password():
    """The plaintext password conftest.py exported as SAGE_AUTH_PASSWORD."""
    import os
    return os.environ.get("SAGE_AUTH_PASSWORD", "test-password-123")


def _auth(tok):
    return {"Authorization": f"Bearer {tok}"}


# --- login ------------------------------------------------------------------

def test_login_success_issues_verifiable_jwt(client):
    resp = client.post(
        "/api/auth/login",
        json={"username": security.AUTH_USERNAME, "password": _login_password()},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["token_type"] == "bearer"
    assert body["username"] == security.AUTH_USERNAME
    assert body["expires_in"] > 0
    assert body["refresh_token"]
    claims = security.decode_token(body["access_token"])
    assert claims["sub"] == security.AUTH_USERNAME
    assert claims["iss"] == security.JWT_ISSUER
    assert claims["aud"] == security.JWT_AUDIENCE
    assert claims["typ"] == "access"
    refresh_claims = security.decode_token(body["refresh_token"], expected_typ="refresh")
    assert refresh_claims["sub"] == security.AUTH_USERNAME
    assert refresh_claims["exp"] > claims["exp"]  # refresh outlives access


def test_refresh_exchanges_refresh_token_for_new_access_token(client):
    login = client.post(
        "/api/auth/login",
        json={"username": security.AUTH_USERNAME, "password": _login_password()},
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
    assert body["refresh_token"] != login["refresh_token"]  # rotation
    claims = security.decode_token(body["access_token"])
    assert claims["sub"] == security.AUTH_USERNAME
    assert claims["typ"] == "access"


def test_refresh_rejects_access_token_used_as_refresh(client):
    """A stolen access token must not work against /api/auth/refresh."""
    login = client.post(
        "/api/auth/login",
        json={"username": security.AUTH_USERNAME, "password": _login_password()},
    ).json()
    resp = client.post(
        "/api/auth/refresh",
        headers={"Authorization": f"Bearer {login['access_token']}"},
    )
    assert resp.status_code == 401


def test_refresh_rejects_garbage_and_missing_tokens(client):
    resp = client.post("/api/auth/refresh", headers={"Authorization": "Bearer not.a.jwt"})
    assert resp.status_code == 401
    resp = client.post("/api/auth/refresh")
    assert resp.status_code == 401


def test_refresh_rejects_expired_refresh_token(client):
    expired = security.create_refresh_token("admin")
    # Re-sign with the same claims but exp in the past by decoding/re-encoding.
    import time as _time
    import jwt as _jwt
    claims = _jwt.decode(
        expired,
        security.JWT_SECRET,
        algorithms=[security.JWT_ALGORITHM],
        audience=security.JWT_AUDIENCE,
        issuer=security.JWT_ISSUER,
    )
    claims["exp"] = int(_time.time()) - 10
    expired = _jwt.encode(claims, security.JWT_SECRET, algorithm=security.JWT_ALGORITHM)
    resp = client.post("/api/auth/refresh", headers={"Authorization": f"Bearer {expired}"})
    assert resp.status_code == 401


def test_login_wrong_password_401_generic_message(client):
    resp = client.post(
        "/api/auth/login",
        json={"username": security.AUTH_USERNAME, "password": "definitely-wrong"},
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
        json={"username": security.AUTH_USERNAME, "password": _login_password()},
    )
    assert resp.status_code == 429


# --- route protection ---------------------------------------------------------

def test_health_and_root_stay_public(client):
    assert client.get("/health").status_code == 200
    assert client.get("/").status_code == 200


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


def test_protected_api_rejects_garbage_token(client):
    resp = client.get("/api/lots", headers=_auth("not.a.jwt"))
    assert resp.status_code == 401


def test_protected_api_rejects_wrong_scheme(client):
    resp = client.get("/api/lots", headers={"Authorization": "Basic dXNlcjpwYXNz"})
    assert resp.status_code == 401


def test_expired_token_rejected(client):
    expired = security.create_access_token(subject="admin", expires_minutes=-1)
    resp = client.get("/api/lots", headers=_auth(expired))
    assert resp.status_code == 401


def test_alg_none_token_rejected(client):
    """The classic JWT confusion attack: unsigned token must not authenticate."""
    forged = pyjwt.encode(
        {"sub": "admin", "iat": int(time.time()), "exp": int(time.time()) + 600},
        key="",
        algorithm="none",
    )
    resp = client.get("/api/lots", headers=_auth(forged))
    assert resp.status_code == 401


def test_wrong_key_token_rejected(client):
    forged = pyjwt.encode(
        {
            "sub": "admin",
            "iat": int(time.time()),
            "exp": int(time.time()) + 600,
            "iss": security.JWT_ISSUER,
            "aud": security.JWT_AUDIENCE,
        },
        key="attacker-controlled-secret",
        algorithm="HS256",
    )
    resp = client.get("/api/lots", headers=_auth(forged))
    assert resp.status_code == 401


def test_login_endpoint_itself_stays_public(client):
    # Earlier tests may have exhausted the shared in-memory rate limiter.
    security._failed_logins.clear()
    # Wrong credentials (401), not a guard rejection — proves the route is open.
    resp = client.post("/api/auth/login", json={"username": "admin", "password": "nope"})
    assert resp.status_code == 401
    assert "authenticate" not in resp.json()["detail"].lower()


# --- token shape ----------------------------------------------------------------

def test_token_contains_jti_and_nbf(token):
    claims = security.decode_token(token)
    assert claims["jti"]
    assert claims["nbf"] <= claims["exp"]


def test_password_verify_roundtrip():
    h = security.hash_password("s3cret-value")
    assert security.is_bcrypt_hash(h)
    assert security.verify_password("s3cret-value", h) is True
    assert security.verify_password("other", h) is False
