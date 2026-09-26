"""
"""
Authentication router: login endpoint, token validation dependency, and refresh flow.
SAGE / SIH 2026 — Semiconductor Burn-In & Latent Defect Screening Console
Team: BINARY BADDIES
"""

from __future__ import annotations

import logging
from typing import Optional

import jwt
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.security import (
    ACCESS_TOKEN_EXPIRE_MINUTES,
    create_access_token,
    decode_access_token,
)
from app.models.user import User, VALID_ROLES
from app.schemas.auth import LoginRequest, TokenResponse, UserResponse
from app.security import (
    JWT_SECRET,
    USERS,
    create_refresh_token,
    decode_token,
    get_user_role,
    hash_password,
    is_bcrypt_hash,
    is_rate_limited,
    record_failed_login,
    reset_failed_logins,
    verify_password,
    hmac_compare,
)
from app.services import auth_service, audit_service

logger = logging.getLogger("sage.auth_api")

router = APIRouter(prefix="/api/auth", tags=["Authentication"])
security_bearer = HTTPBearer(auto_error=False)

        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password.",
            headers={"WWW-Authenticate": "Bearer"},
        )

def _client_key(request: Request) -> str:
    """Best-effort client identity for rate limiting (single-node deploy)."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _check_password(username: str, password: str) -> bool:
    """Verify against the user store (SAGE_USERS or legacy single-admin)."""
    if USERS:
        user = USERS.get(username)
        if user is None:
            return False
        stored = user.get("password") or ""
        if not stored:
            return False
        if is_bcrypt_hash(stored):
            return verify_password(password, stored)
        return hmac_compare(password, stored)

    user = auth_service.get_user_by_username(username)
    if user is None:
        return False
    return auth_service.authenticate_user(username, password) is not None


def get_current_user(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security_bearer),
) -> User:
    """FastAPI dependency to extract and validate Bearer token."""
    if not credentials or not credentials.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication token required.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = credentials.credentials
    try:
        payload = decode_access_token(token)
        username = payload.get("sub")
        if not username:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token payload.",
                headers={"WWW-Authenticate": "Bearer"},
            )
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Access token has expired.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except jwt.InvalidTokenError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid access token.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except Exception as exc:
        logger.debug("Token validation failure: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user = auth_service.get_user_by_username(username)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User account not found.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User account is deactivated.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user_role = (getattr(user, "role", None) or "").strip().lower()
    if not user_role or user_role not in VALID_ROLES:
        logger.warning("Access denied: User '%s' has unassigned or invalid role '%s'", user.username, getattr(user, "role", None))
        client_ip = audit_service.get_client_ip(request)
        audit_service.record_audit_event(
            event_type="PERMISSION_FAILURE",
            event_status="FAILURE",
            endpoint=request.url.path if request else "/api",
            http_method=request.method if request else "GET",
            user_id=user.id,
            username=user.username,
            role=user_role or None,
            client_ip=client_ip,
            details={"reason": "Invalid or unassigned role"},
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access forbidden: user role is invalid or unassigned.",
        )

    return user


def require_roles(*allowed_roles: str):
    """Authorization dependency verifying that the authenticated user possesses an allowed role."""
    valid_allowed = [r.strip().lower() for r in allowed_roles if r and r.strip()]

    def _role_checker(request: Request, user: User = Depends(get_current_user)) -> User:
        user_role = (getattr(user, "role", None) or "").strip().lower()
        client_ip = audit_service.get_client_ip(request)
        endpoint_path = request.url.path if request else "/api"
        method_str = request.method if request else "GET"

        if not user_role or user_role not in VALID_ROLES:
            logger.warning("Access denied: User '%s' has unassigned/invalid role: '%s'", user.username, user_role)
            audit_service.record_audit_event(
                event_type="PERMISSION_FAILURE",
                event_status="FAILURE",
                endpoint=endpoint_path,
                http_method=method_str,
                user_id=user.id,
                username=user.username,
                role=user_role or None,
                client_ip=client_ip,
                details={"reason": "Invalid or unassigned role", "endpoint": endpoint_path},
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access forbidden: user role is invalid or unassigned.",
            )
        if valid_allowed and user_role not in valid_allowed:
            logger.info("Access forbidden: User '%s' with role '%s' lacks required role: %s", user.username, user_role, valid_allowed)
            audit_service.record_audit_event(
                event_type="PERMISSION_FAILURE",
                event_status="FAILURE",
                endpoint=endpoint_path,
                http_method=method_str,
                user_id=user.id,
                username=user.username,
                role=user_role,
                client_ip=client_ip,
                details={"allowed_roles": valid_allowed, "attempted_role": user_role, "endpoint": endpoint_path},
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access forbidden: insufficient role permissions.",
            )
        return user

    return _role_checker


@router.post(
    "/login",
    response_model=TokenResponse,
    summary="Authenticate user and receive JWT access and refresh tokens",
)
def login(req: Request, body: LoginRequest):
    """Authenticate the caller and return access + refresh tokens."""
    if not JWT_SECRET:
        logger.error("Login attempted but SAGE_JWT_SECRET is not configured")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Auth not configured — set SAGE_JWT_SECRET on the server.",
        )

    key = _client_key(req)
    if is_rate_limited(key):
        logger.warning("Rate-limited login attempt from %s", key)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many failed attempts. Try again in a few minutes.",
        )

    username = body.username.strip()
    password = body.password
    client_ip = audit_service.get_client_ip(req)

    if USERS:
        user_payload = USERS.get(username)
        if user_payload is None or not _check_password(username, password):
            record_failed_login(key)
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid username or password.",
                headers={"WWW-Authenticate": "Bearer"},
            )
        role = (user_payload.get("role") or "reviewer").strip().lower()
        if role not in VALID_ROLES:
            role = "reviewer"
    else:
        user = auth_service.authenticate_user(username, password)
        if not user:
            logger.info("Failed login attempt for user: %s", username)
            attempted_user = auth_service.get_user_by_username(username)
            audit_service.record_audit_event(
                event_type="LOGIN",
                event_status="FAILURE",
                endpoint="/api/auth/login",
                http_method="POST",
                user_id=attempted_user.id if attempted_user else None,
                username=username,
                role=attempted_user.role if attempted_user else None,
                client_ip=client_ip,
                details={"reason": "Invalid credentials"},
            )
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid username or password.",
                headers={"WWW-Authenticate": "Bearer"},
            )
        role = (getattr(user, "role", None) or "reviewer").strip().lower()

    reset_failed_logins(key)
    access_token = create_access_token(subject=username, role=role)
    refresh_token = create_refresh_token(subject=username, role=role)

    payload = decode_token(access_token)
    if not USERS:
        audit_service.record_audit_event(
            event_type="LOGIN",
            event_status="SUCCESS",
            endpoint="/api/auth/login",
            http_method="POST",
            user_id=auth_service.get_user_by_username(username).id if auth_service.get_user_by_username(username) else None,
            username=username,
            role=role,
            client_ip=client_ip,
            details={"message": "Authentication successful"},
        )

    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        token_type="bearer",
        expires_in=payload["exp"] - payload["iat"],
        username=username,
        role=role,
    )


@router.post("/refresh", response_model=TokenResponse)
def refresh(request: Request) -> TokenResponse:
    """Trade a valid refresh token for a fresh access token and rotated refresh token."""
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
    """Return user identity from the current access token."""
    return {
        "username": current.get("sub") if isinstance(current, dict) else current.username,
        "role": current.get("role") if isinstance(current, dict) else getattr(current, "role", None),
        "authenticated": True,
    }


@router.post("/logout")
def logout_user(req: Request, user: User = Depends(get_current_user)):
    """Log out the current authenticated user and record the audit event."""
    client_ip = audit_service.get_client_ip(req)
    audit_service.record_audit_event(
        event_type="LOGOUT",
        event_status="SUCCESS",
        endpoint="/api/auth/logout",
        http_method="POST",
        user_id=user.id,
        username=user.username,
        role=user.role,
        client_ip=client_ip,
        details={"message": "User logged out successfully"},
    )
    return {
        "message": "Logged out successfully",
        "username": user.username,
        "role": user.role,
    }


@router.get(
    "/admin-check",
    response_model=UserResponse,
    summary="Admin-only boundary check endpoint",
)
def admin_boundary_check(user: User = Depends(require_roles("admin"))):
    """Protected admin boundary verification endpoint."""
    return UserResponse(
        id=user.id,
        username=user.username,
        full_name=user.full_name,
        role=user.role,
        is_active=user.is_active,
    )

