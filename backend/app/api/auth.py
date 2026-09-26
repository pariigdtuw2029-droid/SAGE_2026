"""
Authentication router: login endpoint and token validation dependency.
SAGE / SIH 2026 — Semiconductor Burn-In & Latent Defect Screening Console
Team: BINARY BADDIES
"""

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
from app.services import auth_service, audit_service

logger = logging.getLogger("sage.auth_api")

router = APIRouter(prefix="/api/auth", tags=["Authentication"])
security_bearer = HTTPBearer(auto_error=False)


def get_current_user(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security_bearer),
) -> User:
    """
    FastAPI dependency to extract and validate Bearer token.
    Validates token signature and expiration, retrieves user identity from server DB,
    and returns the authenticated User instance.
    Raises 401 on missing, expired, invalid, or deactivated tokens.
    """
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
    """
    Authorization dependency verifying that the authenticated user possesses an allowed role.
    Raises 401 if unauthenticated or deactivated (handled by get_current_user).
    Raises 403 Forbidden if user possesses an insufficient or invalid role.
    """
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
    summary="Authenticate user and receive JWT access token",
)
def login(req: Request, request: LoginRequest):
    """
    Authenticate engineer credentials against stored password hash.
    Returns signed Bearer access token on success.
    Returns 401 on invalid credentials with a generic message.
    """
    username = request.username.strip()
    password = request.password
    client_ip = audit_service.get_client_ip(req)

    # Brute-force guard: reject IPs that exhausted their failed-login budget
    # before doing any credential work.
    if auth_service.is_login_rate_limited(client_ip):
        logger.warning("Login rate-limited for IP %s (username attempted: %s)", client_ip, username)
        audit_service.record_audit_event(
            event_type="LOGIN",
            event_status="FAILURE",
            endpoint="/api/auth/login",
            http_method="POST",
            username=username,
            role=None,
            client_ip=client_ip,
            details={"reason": "Rate limited"},
        )
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many failed login attempts. Please try again later.",
            headers={"Retry-After": str(auth_service.login_retry_after_seconds(client_ip))},
        )

    user = auth_service.authenticate_user(username, password)
    if not user:
        logger.info("Failed login attempt for user: %s", username)
        attempted_user = auth_service.get_user_by_username(username)
        auth_service.record_failed_login(client_ip)
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

    auth_service.clear_failed_logins(client_ip)
    audit_service.record_audit_event(
        event_type="LOGIN",
        event_status="SUCCESS",
        endpoint="/api/auth/login",
        http_method="POST",
        user_id=user.id,
        username=user.username,
        role=user.role,
        client_ip=client_ip,
        details={"message": "Authentication successful"},
    )

    token = create_access_token(subject=user.username)
    return TokenResponse(
        access_token=token,
        token_type="bearer",
        expires_in=ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )


@router.post(
    "/logout",
    summary="Logout current user and record audit event",
)
def logout_user(
    req: Request,
    user: User = Depends(get_current_user),
):
    """
    Authenticated logout endpoint.
    Records LOGOUT / SUCCESS event in the append-only audit trail.
    """
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
    return {"message": "Logged out successfully"}


@router.get(
    "/me",
    response_model=UserResponse,
    summary="Get current authenticated user profile",
)
def get_current_user_profile(user: User = Depends(get_current_user)):
    """
    Protected verification endpoint.
    Requires valid Bearer access token.
    Returns profile information and authoritative server role for the authenticated user.
    """
    return UserResponse(
        id=user.id,
        username=user.username,
        full_name=user.full_name,
        role=user.role,
        is_active=user.is_active,
    )


@router.get(
    "/admin-check",
    response_model=UserResponse,
    summary="Admin-only boundary check endpoint",
)
def admin_boundary_check(user: User = Depends(require_roles("admin"))):
    """
    Protected admin boundary verification endpoint.
    Requires valid Bearer access token with admin role.
    Returns 403 Forbidden for non-admin roles (engineer, reviewer).
    """
    return UserResponse(
        id=user.id,
        username=user.username,
        full_name=user.full_name,
        role=user.role,
        is_active=user.is_active,
    )

