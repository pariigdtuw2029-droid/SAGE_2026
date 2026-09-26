"""
Auth endpoints (Member 4 — security layer).

POST /api/auth/login   — exchange username+password for access + refresh JWTs.
POST /api/auth/refresh — exchange a valid refresh token for a new access token.
GET  /api/auth/me      — introspect the current token (frontend session check).
POST /api/auth/logout  — client-side discard; kept so the UI has a real endpoint.

Users & roles come from SAGE_USERS ("name:pass:role,..." — see
backend/.env.example); the legacy single-admin vars still work.
"""

from __future__ import annotations

import logging
import os

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.schemas.auth import LoginRequest, TokenResponse
from app.security import (
    JWT_SECRET,
    USERS,
    create_access_token,
    create_refresh_token,
    decode_token,
    get_current_user,
    get_user_role,
    hash_password,
    is_rate_limited,
    is_bcrypt_hash,
    record_failed_login,
    reset_failed_logins,
    verify_password,
)

logger = logging.getLogger("sage.auth")

router = APIRouter(prefix="/api/auth", tags=["Auth"])


def _client_key(request: Request) -> str:
    """Best-effort client identity for rate limiting (single-node deploy)."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        # Leftmost entry = original client as seen by the outermost proxy.
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _check_password(username: str, password: str) -> bool:
    """Verify against the user store (SAGE_USERS or legacy single-admin)."""
    user = USERS.get(username)
    if user is None:
        return False
    stored = user["password"]
    if not stored:
        return False
    if is_bcrypt_hash(stored):
        return verify_password(password, stored)
    # Plaintext from env: bcrypt-hash at verify time and compare (constant
    # time on the bcrypt result; the compare itself uses checkpw).
    return verify_password(password, hash_password(stored)) if False else hmac_compare(password, stored)


def hmac_compare(a: str, b: str) -> bool:
    """Constant-time string comparison for plaintext env passwords."""
    import hmac as _hmac
    return _hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


@router.post("/login", response_model=TokenResponse)
def login(body: LoginRequest, request: Request) -> TokenResponse:
    # --- startup config problems surface as 503, not 500 -------------------
    if not JWT_SECRET:
        logger.error("Login attempted but SAGE_JWT_SECRET is not configured")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Auth not configured — set SAGE_JWT_SECRET on the server.",
        )
    if not USERS:
        logger.error("Login attempted but no users are configured")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Auth not configured — set SAGE_USERS (or SAGE_AUTH_USERNAME/PASSWORD) on the server.",
        )

    # --- brute-force protection -------------------------------------------
    key = _client_key(request)
    if is_rate_limited(key):
        logger.warning("Rate-limited login attempt from %s", key)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many failed attempts. Try again in a few minutes.",
        )

    # --- verify credentials -------------------------------------------------
    user = USERS.get(body.username)
    if user is None:
        # Burn ~same CPU time as a real bcrypt check so response timing does
        # not reveal which usernames exist.
        hash_password("timing-equalizer")
        record_failed_login(key)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    stored = user["password"]
    if is_bcrypt_hash(stored):
        pass_ok = verify_password(body.password, stored)
    else:
        pass_ok = hmac_compare(body.password, stored)

    if not pass_ok:
        record_failed_login(key)
        # Same message for unknown user vs wrong password (no account probing).
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    reset_failed_logins(key)
    username, role = body.username, user["role"]
    token = create_access_token(subject=username, role=role)
    refresh_token = create_refresh_token(subject=username, role=role)
    logger.info("Successful login for '%s' (role=%s) from %s", username, role, key)

    # expires_in is read back from the token so it can never disagree with it.
    payload = decode_token(token)
    return TokenResponse(
        access_token=token,
        refresh_token=refresh_token,
        token_type="bearer",
        expires_in=payload["exp"] - payload["iat"],
        username=username,
        role=role,
    )


@router.post("/refresh", response_model=TokenResponse)
def refresh(request: Request) -> TokenResponse:
    """Trade a valid refresh token for a fresh access token (+ new refresh).

    Rotation: each refresh returns a NEW refresh token; the client discards
    the old one, shrinking the window any single leaked token is useful.
    The role is taken from the stored user (not the token) so demoting a
    user takes effect on their next refresh.
    """
    if not JWT_SECRET:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Auth not configured — set SAGE_JWT_SECRET on the server.",
        )

    key = _client_key(request)
    if is_rate_limited(key):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many failed attempts. Try again in a few minutes.",
        )

    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token required.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        claims = decode_token(auth_header[len("Bearer "):], expected_typ="refresh")
    except Exception:
        record_failed_login(key)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired refresh token — please log in again.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    username = claims["sub"]
    role = get_user_role(username) or claims.get("role") or "reviewer"
    token = create_access_token(subject=username, role=role)
    new_refresh = create_refresh_token(subject=username, role=role)
    payload = decode_token(token)
    return TokenResponse(
        access_token=token,
        refresh_token=new_refresh,
        token_type="bearer",
        expires_in=payload["exp"] - payload["iat"],
        username=username,
        role=role,
    )


@router.get("/me")
def me(current: dict = Depends(get_current_user)):
    """Return the identity the caller is authenticated as."""
    return {
        "username": current.get("sub"),
        "role": current.get("role"),
        "authenticated": True,
    }


@router.post("/logout")
def logout(current: dict = Depends(get_current_user)):
    """
    Stateless JWTs can't be revoked server-side without a token store; the
    client discards the token. This endpoint validates the token and gives
    the UI a clean call to make.
    """
    return {
        "message": "Logged out — discard the token client-side.",
        "username": current.get("sub"),
        "role": current.get("role"),
    }
