"""
JWT authentication + role-based access (Member 4 — security layer).

Design:
- Users come from the SAGE_USERS env var, one "username:password:role"
  triplet per comma-separated entry (see backend/.env.example):

      SAGE_USERS=admin:sage2026:admin,priya:engpass123:engineer,rahul:revpass:reviewer

  Roles: admin (full), engineer (can upload burn-in data), reviewer
  (read-only). Unknown roles fall back to "reviewer" (least privilege).
- Backwards compatibility: if SAGE_USERS is not set, the legacy single-user
  vars (SAGE_AUTH_USERNAME / SAGE_AUTH_PASSWORD / SAGE_AUTH_PASSWORD_HASH)
  still work and map to the "admin" role.
- Passwords are bcrypt-verified at login time and compared via constant-time
  primitives; nothing reversible is stored anywhere.
- Tokens are HS256-signed JWTs (PyJWT) with a short TTL, jti, and pinned
  iss/aud; the secret comes from SAGE_JWT_SECRET (>= 32 chars recommended).
  The algorithm is pinned server-side — the token's own alg header is never
  trusted (no "alg: none" / confusion attacks). Each token carries the
  user's role in the "role" claim.
- In-memory login rate limiting: 5 failures / 5 min per client IP.

Public surface:
    USERS                       -> {username: {"password": str, "role": str}}
    ROLES                       -> allowed role names
    create_access_token(...)    -> str (includes role claim)
    create_refresh_token(...)   -> str
    decode_token(token)         -> dict
    get_current_user            -> FastAPI dependency (raises 401)
    require_roles(*roles)       -> FastAPI dependency (raises 403)
    require_write_access        -> dependency: admin or engineer only
"""

from __future__ import annotations

import hmac
import hashlib
import logging
import os
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import jwt
import bcrypt
from dotenv import load_dotenv
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

logger = logging.getLogger("sage.security")

# Load backend/.env if present (same convention as database/connection.py).
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

# --- JWT settings -----------------------------------------------------------

JWT_SECRET = os.getenv("SAGE_JWT_SECRET", "")
JWT_ALGORITHM = "HS256"  # pinned; tokens naming another alg are rejected
JWT_ISSUER = "sage-api"
JWT_AUDIENCE = "sage-console"


def _expire_minutes() -> int:
    """Access-token TTL in minutes (SAGE_JWT_EXPIRE_MINUTES, default 60)."""
    try:
        return max(1, int(os.getenv("SAGE_JWT_EXPIRE_MINUTES", "60")))
    except ValueError:
        return 60


def _refresh_days() -> int:
    """Refresh-token TTL in days (SAGE_REFRESH_EXPIRE_DAYS, default 7)."""
    try:
        return max(1, int(os.getenv("SAGE_REFRESH_EXPIRE_DAYS", "7")))
    except ValueError:
        return 7


# --- Users & roles ----------------------------------------------------------

VALID_ROLES = ("admin", "engineer", "reviewer")
DEFAULT_ROLE = "reviewer"  # least privilege for malformed role values


def _parse_users(raw: str | None) -> dict[str, dict[str, str]]:
    """Parse SAGE_USERS="name:pass:role,..." into {name: {password, role}}."""
    users: dict[str, dict[str, str]] = {}
    if not raw:
        return users
    for entry in raw.split(","):
        entry = entry.strip()
        if not entry:
            continue
        parts = entry.split(":")
        if len(parts) < 2:
            logger.warning("Ignoring malformed SAGE_USERS entry (need user:pass[:role])")
            continue
        username, password = parts[0].strip(), ":".join(parts[1:-1]) if len(parts) > 2 else parts[1]
        role = parts[-1].strip().lower() if len(parts) >= 3 else DEFAULT_ROLE
        if not username or not password:
            logger.warning("Ignoring SAGE_USERS entry with empty username or password")
            continue
        if role not in VALID_ROLES:
            logger.warning("Unknown role '%s' for user '%s' — demoting to '%s'", role, username, DEFAULT_ROLE)
            role = DEFAULT_ROLE
        users[username] = {"password": password, "role": role}
    return users


def _build_users() -> dict[str, dict[str, str]]:
    users = _parse_users(os.getenv("SAGE_USERS"))
    if users:
        return users
    # Legacy single-user fallback -> admin role.
    if os.getenv("SAGE_AUTH_PASSWORD") or os.getenv("SAGE_AUTH_PASSWORD_HASH"):
        return {
            os.getenv("SAGE_AUTH_USERNAME", "admin"): {
                "password": os.getenv("SAGE_AUTH_PASSWORD", ""),
                "role": "admin",
            }
        }
    return {}


USERS: dict[str, dict[str, str]] = _build_users()


def get_user_role(username: str) -> str | None:
    user = USERS.get(username)
    return user["role"] if user else None


# --- Password hashing ---------------------------------------------------------
# bcrypt directly (passlib is unmaintained and breaks with bcrypt >= 4.1).
# bcrypt only operates on <= 72 bytes; longer inputs are pre-hashed with
# SHA-256 first so long passphrases neither break nor silently truncate.

def _bcrypt_input(plain: str) -> bytes:
    data = plain.encode("utf-8")
    if len(data) > 72:
        data = hashlib.sha256(data).hexdigest().encode("ascii")
    return data


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(_bcrypt_input(plain), bcrypt.gensalt(rounds=12)).decode("ascii")


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(_bcrypt_input(plain), hashed.encode("ascii"))
    except (ValueError, TypeError):
        return False


def is_bcrypt_hash(value: str) -> bool:
    return value.startswith(("$2a$", "$2b$", "$2y$"))

# --- In-memory rate limiting (per-process; fine for a single-node deploy) ---

_MAX_ATTEMPTS = 5
_WINDOW_SECONDS = 300
_failed_logins: dict[str, list[float]] = {}


def _prune_attempts(now: float) -> None:
    for ip, stamps in list(_failed_logins.items()):
        fresh = [t for t in stamps if now - t < _WINDOW_SECONDS]
        if fresh:
            _failed_logins[ip] = fresh
        else:
            _failed_logins.pop(ip, None)


def is_rate_limited(key: str, now: float | None = None) -> bool:
    """True when `key` has >= _MAX_ATTEMPTS failures inside the window."""
    now = now if now is not None else datetime.now(timezone.utc).timestamp()
    _prune_attempts(now)
    return len(_failed_logins.get(key, [])) >= _MAX_ATTEMPTS


def record_failed_login(key: str, now: float | None = None) -> None:
    now = now if now is not None else datetime.now(timezone.utc).timestamp()
    _failed_logins.setdefault(key, []).append(now)


def reset_failed_logins(key: str) -> None:
    _failed_logins.pop(key, None)


# --- Token helpers ----------------------------------------------------------

def create_access_token(
    subject: str,
    role: str | None = None,
    expires_minutes: int | None = None,
    extra_claims: dict[str, Any] | None = None,
) -> str:
    """Mint a short-lived HS256 JWT for `subject`.

    typ="access" distinguishes these from refresh tokens so a refresh
    token can never be replayed as a credential against /api/* routes.
    The user's role rides along in the "role" claim.
    """
    claims: dict[str, Any] = {"role": role or get_user_role(subject) or DEFAULT_ROLE}
    if extra_claims:
        claims.update(extra_claims)
    return _create_token(
        subject,
        minutes=expires_minutes if expires_minutes is not None else _expire_minutes(),
        typ="access",
        extra_claims=claims,
    )


def create_refresh_token(
    subject: str,
    role: str | None = None,
    expires_days: int | None = None,
) -> str:
    """Mint a long-lived token used only to obtain new access tokens."""
    return _create_token(
        subject,
        minutes=_refresh_days() * 24 * 60,
        typ="refresh",
        extra_claims={"role": role or get_user_role(subject) or DEFAULT_ROLE},
    )


def _create_token(
    subject: str,
    minutes: int,
    typ: str,
    extra_claims: dict[str, Any] | None = None,
) -> str:
    if not JWT_SECRET:
        raise RuntimeError(
            "SAGE_JWT_SECRET is not set — refusing to mint tokens. "
            "Generate one with: python -c 'import secrets; print(secrets.token_urlsafe(48))'"
        )
    now = datetime.now(timezone.utc)
    payload: dict[str, Any] = {
        "sub": subject,
        "iat": int(now.timestamp()),
        "nbf": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=minutes)).timestamp()),
        "iss": JWT_ISSUER,
        "aud": JWT_AUDIENCE,
        "typ": typ,
        "jti": secrets.token_hex(8),  # unique token id, useful for audit logs
    }
    if extra_claims:
        payload.update(extra_claims)
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def decode_token(token: str, expected_typ: str = "access") -> dict[str, Any]:
    """Decode + verify signature/exp/aud/iss/typ. Raises jwt.* on any problem.

    expected_typ stops a refresh token from being used as an access token
    (and vice versa) even though both are signed with the same key.
    """
    payload = jwt.decode(
        token,
        JWT_SECRET,
        algorithms=[JWT_ALGORITHM],  # pinned — never trust the token's alg
        audience=JWT_AUDIENCE,
        issuer=JWT_ISSUER,
        options={"require": ["exp", "sub", "iat"]},
    )
    if payload.get("typ") != expected_typ:
        raise jwt.InvalidTokenError(f"wrong token type: expected {expected_typ}")
    return payload


# --- FastAPI dependencies ---------------------------------------------------

_bearer = HTTPBearer(auto_error=False)


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> dict[str, Any]:
    """
    Dependency guarding protected routes.

    Returns the decoded claims ({"sub", "role", "jti", ...}) on success.
    Raises 401 with WWW-Authenticate: Bearer on any failure.
    """
    if credentials is None or not credentials.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = credentials.credentials
    try:
        payload = decode_token(token)
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token expired — please log in again.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except jwt.InvalidTokenError:
        # One generic message for tampered/malformed/wrong-key tokens so
        # attackers learn nothing about which check failed.
        logger.warning("Rejected invalid JWT (bad signature or malformed)")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not payload.get("sub"):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return payload


# Alias so route modules can read naturally.
require_auth = get_current_user


def require_roles(*allowed: str):
    """Dependency factory: 403 unless the token's role is in `allowed`."""
    allowed_set = {r.lower() for r in allowed}

    def dependency(current: dict = Depends(get_current_user)) -> dict:
        role = (current.get("role") or "").lower()
        if role not in allowed_set:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Forbidden — requires role: {' or '.join(sorted(allowed_set))}.",
            )
        return current

    return dependency


def require_write_access(current: dict = Depends(get_current_user)) -> dict:
    """Uploads / mutations: admins and engineers; reviewers are read-only."""
    role = (current.get("role") or "").lower()
    if role not in ("admin", "engineer"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden — reviewers have read-only access.",
        )
    return current
