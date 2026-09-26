"""
JWT authentication (Member 4 — security layer).

Design:
- Single admin/operator account from env vars (SAGE_AUTH_USERNAME /
  SAGE_AUTH_PASSWORD / SAGE_AUTH_PASSWORD_HASH, see backend/.env.example).
  No user table for a single-team deployment; add one only if multiple
  roles are required.
- SAGE_AUTH_PASSWORD (plaintext in .env) is bcrypt-hashed at login time and
  compared in constant time. SAGE_AUTH_PASSWORD_HASH (a $2b$... bcrypt
  string) is accepted as-is, so nothing reversible ever has to be stored.
- Tokens are HS256-signed JWTs (PyJWT) with a short TTL, jti, and pinned
  iss/aud; the secret comes from SAGE_JWT_SECRET and must be set (>= 32
  chars recommended). The algorithm is pinned server-side — the token's
  own alg header is never trusted (no "alg: none" / confusion attacks).
- In-memory login rate limiting: 5 failures / 5 min per client IP.

Public surface:
    create_access_token(sub, ...) -> str
    decode_token(token)           -> dict
    get_current_user              -> FastAPI dependency (raises 401)
    require_auth                  -> alias of get_current_user
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
    """Token TTL in minutes (SAGE_JWT_EXPIRE_MINUTES, default 60)."""
    try:
        return max(1, int(os.getenv("SAGE_JWT_EXPIRE_MINUTES", "60")))
    except ValueError:
        return 60


# --- Credential settings ----------------------------------------------------

AUTH_USERNAME = os.getenv("SAGE_AUTH_USERNAME", "admin")
AUTH_PASSWORD_HASH = os.getenv("SAGE_AUTH_PASSWORD_HASH", "")

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
    expires_minutes: int | None = None,
    extra_claims: dict[str, Any] | None = None,
) -> str:
    """Mint a signed JWT for `subject` (the username)."""
    if not JWT_SECRET:
        raise RuntimeError(
            "SAGE_JWT_SECRET is not set — refusing to mint tokens. "
            "Generate one with: python -c 'import secrets; print(secrets.token_urlsafe(48))'"
        )
    now = datetime.now(timezone.utc)
    ttl = expires_minutes if expires_minutes is not None else _expire_minutes()
    payload: dict[str, Any] = {
        "sub": subject,
        "iat": int(now.timestamp()),
        "nbf": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=ttl)).timestamp()),
        "iss": JWT_ISSUER,
        "aud": JWT_AUDIENCE,
        "jti": secrets.token_hex(8),  # unique token id, useful for audit logs
    }
    if extra_claims:
        payload.update(extra_claims)
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def decode_token(token: str) -> dict[str, Any]:
    """Decode + verify signature/exp/aud/iss. Raises jwt.* on any problem."""
    return jwt.decode(
        token,
        JWT_SECRET,
        algorithms=[JWT_ALGORITHM],  # pinned — never trust the token's alg
        audience=JWT_AUDIENCE,
        issuer=JWT_ISSUER,
        options={"require": ["exp", "sub", "iat"]},
    )


# --- Password helpers -------------------------------------------------------

def is_bcrypt_hash(value: str) -> bool:
    return value.startswith(("$2a$", "$2b$", "$2y$"))


# --- FastAPI dependency -----------------------------------------------------

_bearer = HTTPBearer(auto_error=False)


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> dict[str, Any]:
    """
    Dependency guarding protected routes.

    Returns the decoded claims ({"sub": <username>, "jti": ...}) on success.
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
