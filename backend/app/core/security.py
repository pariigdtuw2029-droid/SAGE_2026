"""
Authentication security utilities: password hashing and JWT token handling.
SAGE / SIH 2026 — Semiconductor Burn-In & Latent Defect Screening Console
Team: BINARY BADDIES
"""

import os
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

import bcrypt
import jwt

logger = logging.getLogger("sage.security")

ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("SAGE_ACCESS_TOKEN_EXPIRE_MINUTES", "480"))  # 8 hours default


def get_jwt_secret() -> str:
    """
    Retrieve the JWT signing secret from the environment.
    Raises RuntimeError if SAGE_JWT_SECRET is not configured or empty,
    preventing the application from silently running with a known, hardcoded,
    or deterministic fallback key.
    """
    secret = os.getenv("SAGE_JWT_SECRET")
    if not secret or not secret.strip():
        raise RuntimeError(
            "CRITICAL SECURITY CONFIGURATION ERROR: 'SAGE_JWT_SECRET' environment variable is missing or empty. "
            "SAGE cannot sign or verify authentication tokens without a configured signing secret. "
            "Please configure 'SAGE_JWT_SECRET' in your environment or production secrets manager."
        )
    return secret.strip()


def validate_security_config() -> None:
    """
    Validate that all required security configuration parameters are present.
    Called during application startup lifespan.
    """
    _ = get_jwt_secret()



def hash_password(password: str) -> str:
    """Hash a password using bcrypt with a random salt."""
    if not password:
        raise ValueError("Password cannot be empty.")
    pwd_bytes = password.encode("utf-8")
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(pwd_bytes, salt).decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a plaintext password against a bcrypt hash in constant time."""
    if not plain_password or not hashed_password:
        return False
    try:
        return bcrypt.checkpw(
            plain_password.encode("utf-8"),
            hashed_password.encode("utf-8"),
        )
    except Exception as exc:
        logger.debug("Password verification error: %s", exc)
        return False


def create_access_token(
    subject: str,
    expires_delta: Optional[timedelta] = None,
    extra_claims: Optional[Dict[str, Any]] = None,
) -> str:
    """
    Generate a signed JWT access token.
    Claims: sub, iat, exp (and optional minimal extra claims).
    """
    now = datetime.now(timezone.utc)
    if expires_delta:
        expire = now + expires_delta
    else:
        expire = now + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)

    payload: Dict[str, Any] = {
        "sub": str(subject),
        "iat": int(now.timestamp()),
        "exp": int(expire.timestamp()),
    }
    if extra_claims:
        payload.update(extra_claims)

    secret = get_jwt_secret()
    encoded_jwt = jwt.encode(payload, secret, algorithm=ALGORITHM)
    return encoded_jwt


def decode_access_token(token: str) -> Dict[str, Any]:
    """
    Decode and validate a JWT access token.
    Raises jwt.ExpiredSignatureError if expired.
    Raises jwt.InvalidTokenError on invalid signature/payload.
    """
    secret = get_jwt_secret()
    return jwt.decode(
        token,
        secret,
        algorithms=[ALGORITHM],
        options={"require": ["sub", "iat", "exp"]},
    )


def __getattr__(name: str):
    """
    Dynamically resolve SAGE_JWT_SECRET to get_jwt_secret() for module-level compatibility,
    ensuring it always evaluates the live environment variable and raises RuntimeError if unset.
    """
    if name == "SAGE_JWT_SECRET":
        return get_jwt_secret()
    raise AttributeError(f"module '{__name__}' has no attribute '{name}'")
