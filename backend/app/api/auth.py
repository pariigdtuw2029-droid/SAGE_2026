"""
Auth endpoints (Member 4 — security layer).

POST /api/auth/login  — exchange username+password for a short-lived JWT.
GET  /api/auth/me     — introspect the current token (frontend session check).
POST /api/auth/logout — client-side discard; kept so the UI has a real endpoint.
"""

from __future__ import annotations

import hmac
import logging
import os

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.schemas.auth import LoginRequest, TokenResponse
from app.security import (
    JWT_SECRET,
    AUTH_USERNAME,
    create_access_token,
    decode_token,
    get_current_user,
    hash_password,
    is_rate_limited,
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


@router.post("/login", response_model=TokenResponse)
def login(body: LoginRequest, request: Request) -> TokenResponse:
    # --- startup config problems surface as 503, not 500 -------------------
    if not JWT_SECRET:
        logger.error("Login attempted but SAGE_JWT_SECRET is not configured")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Auth not configured — set SAGE_JWT_SECRET on the server.",
        )

    # --- brute-force protection -------------------------------------------
    key = _client_key(request)
    if is_rate_limited(key):
        logger.warning("Rate-limited login attempt from %s", key)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many failed attempts. Try again in a few minutes.",
        )

    # --- resolve the stored credential -------------------------------------
    if os.getenv("SAGE_AUTH_PASSWORD_HASH"):
        stored_hash = os.environ["SAGE_AUTH_PASSWORD_HASH"]
    elif os.getenv("SAGE_AUTH_PASSWORD"):
        stored_hash = hash_password(os.environ["SAGE_AUTH_PASSWORD"])
    else:
        logger.error("Login attempted but no password credential is configured")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Auth not configured — set SAGE_AUTH_PASSWORD or SAGE_AUTH_PASSWORD_HASH on the server.",
        )

    user_ok = hmac.compare_digest(body.username.encode("utf-8"), AUTH_USERNAME.encode("utf-8"))
    pass_ok = verify_password(body.password, stored_hash)

    if not (user_ok and pass_ok):
        record_failed_login(key)
        # Same message for unknown user vs wrong password (no account probing).
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    reset_failed_logins(key)
    token = create_access_token(subject=AUTH_USERNAME)
    logger.info("Successful login for '%s' from %s", AUTH_USERNAME, key)

    # expires_in is read back from the token so it can never disagree with it.
    payload = decode_token(token)
    return TokenResponse(
        access_token=token,
        token_type="bearer",
        expires_in=payload["exp"] - payload["iat"],
        username=AUTH_USERNAME,
    )


@router.get("/me")
def me(current: dict = Depends(get_current_user)):
    """Return the username the caller is authenticated as."""
    return {"username": current.get("sub"), "authenticated": True}


@router.post("/logout")
def logout(current: dict = Depends(get_current_user)):
    """
    Stateless JWTs can't be revoked server-side without a token store; the
    client discards the token. This endpoint validates the token and gives
    the UI a clean call to make.
    """
    return {"message": "Logged out — discard the token client-side.", "username": current.get("sub")}
