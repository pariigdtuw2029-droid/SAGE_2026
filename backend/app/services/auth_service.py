"""
Authentication service layer: User CRUD, verification, and demo seeding.
SAGE / SIH 2026 — Semiconductor Burn-In & Latent Defect Screening Console
Team: BINARY BADDIES
"""

import logging
import time
from collections import defaultdict
from typing import Optional

from sqlalchemy import select, func, inspect, text
from sqlalchemy.orm import Session

from app.core.security import hash_password, verify_password
from app.models.user import User, VALID_ROLES
from database.connection import SessionLocal, engine

logger = logging.getLogger("sage.auth_service")

# Default demo credentials for development/testing
DEFAULT_DEMO_USERNAME = "admin"
DEFAULT_DEMO_PASSWORD = "sage2026"
DEFAULT_DEMO_FULL_NAME = "SAGE Test Engineer"
DEFAULT_DEMO_ROLE = "admin"

# --- Login brute-force protection (Phase 12.2) -------------------------------
# In-memory sliding-window lockout, keyed by client IP: after
# LOGIN_MAX_FAILED_ATTEMPTS failed logins within LOGIN_WINDOW_SECONDS,
# further attempts from that IP are rejected with 429 until the window
# rolls off. Cleared for the IP on a successful login. Single-process
# scope is deliberate: the API runs as one uvicorn worker on Render.
LOGIN_MAX_FAILED_ATTEMPTS = 5
LOGIN_WINDOW_SECONDS = 300  # 5 minutes
_failed_logins: dict[str, list[float]] = defaultdict(list)


def is_login_rate_limited(client_ip: str) -> bool:
    """True if this IP has exhausted its failed-login budget in the window."""
    now = time.monotonic()
    attempts = [t for t in _failed_logins.get(client_ip, []) if now - t < LOGIN_WINDOW_SECONDS]
    _failed_logins[client_ip] = attempts  # drop expired entries
    return len(attempts) >= LOGIN_MAX_FAILED_ATTEMPTS


def record_failed_login(client_ip: str) -> int:
    """Record a failed login for this IP; returns current failure count."""
    now = time.monotonic()
    attempts = [t for t in _failed_logins.get(client_ip, []) if now - t < LOGIN_WINDOW_SECONDS]
    attempts.append(now)
    _failed_logins[client_ip] = attempts
    return len(attempts)


def clear_failed_logins(client_ip: str) -> None:
    """Reset the failed-login budget for this IP (called on success)."""
    _failed_logins.pop(client_ip, None)


def login_retry_after_seconds(client_ip: str) -> int:
    """Seconds until this IP may retry (for the 429 Retry-After header)."""
    attempts = _failed_logins.get(client_ip, [])
    if not attempts:
        return 0
    oldest = min(attempts)
    remaining = int(LOGIN_WINDOW_SECONDS - (time.monotonic() - oldest)) + 1
    return max(remaining, 1)


def init_auth_db(target_engine=None) -> None:
    """
    Ensure the users table exists in the current database engine.
    Applies additive non-destructive migration for role column if missing.
    If the users table is empty, seeds the default development admin user.
    """
    eng = target_engine or engine
    try:
        User.__table__.create(bind=eng, checkfirst=True)
        inspector = inspect(eng)
        columns = [c["name"] for c in inspector.get_columns("users")]
        if "role" not in columns:
            with eng.connect() as conn:
                conn.execute(text("ALTER TABLE users ADD COLUMN role VARCHAR(32) DEFAULT 'engineer' NOT NULL;"))
                conn.commit()
            logger.info("Added 'role' column to 'users' table via additive migration.")

        with SessionLocal() as db:
            demo_accounts = [
                {"username": DEFAULT_DEMO_USERNAME, "password": DEFAULT_DEMO_PASSWORD, "full_name": DEFAULT_DEMO_FULL_NAME, "role": DEFAULT_DEMO_ROLE},
                {"username": "engineer", "password": DEFAULT_DEMO_PASSWORD, "full_name": "SAGE Burn-In Engineer", "role": "engineer"},
                {"username": "reviewer", "password": DEFAULT_DEMO_PASSWORD, "full_name": "SAGE Quality Reviewer", "role": "reviewer"},
            ]
            for acc in demo_accounts:
                user_record = db.scalar(select(User).where(User.username == acc["username"]))
                if not user_record:
                    new_user = User(
                        username=acc["username"],
                        hashed_password=hash_password(acc["password"]),
                        full_name=acc["full_name"],
                        role=acc["role"],
                        is_active=True,
                    )
                    db.add(new_user)
                    db.commit()
                    logger.info("Seeded demo user '%s' with role '%s'.", acc["username"], acc["role"])
                else:
                    if not getattr(user_record, "role", None) or user_record.role != acc["role"]:
                        user_record.role = acc["role"]
                        db.commit()
    except Exception as exc:
        logger.warning("Auth DB initialization notice: %s", exc)



def get_user_by_username(username: str, db: Optional[Session] = None) -> Optional[User]:
    """Retrieve user by exact username. Case-sensitive."""
    if not username:
        return None
    
    clean_user = username.strip()
    if not clean_user:
        return None

    def _query(session: Session) -> Optional[User]:
        stmt = select(User).where(User.username == clean_user)
        return session.scalar(stmt)

    if db is not None:
        try:
            return _query(db)
        except Exception:
            init_auth_db()
            return _query(db)
    
    with SessionLocal() as session:
        try:
            return _query(session)
        except Exception:
            init_auth_db()
            return _query(session)


def authenticate_user(username: str, password: str, db: Optional[Session] = None) -> Optional[User]:
    """
    Verify user credentials against stored bcrypt hash.
    Returns User if valid and active, else None.
    Generic failure handling prevents leaking account existence.
    """
    if not username or not password:
        return None

    user = get_user_by_username(username, db=db)
    if not user:
        return None
    
    if not user.is_active:
        logger.warning("Attempted login to inactive account: %s", username)
        return None

    if not verify_password(password, user.hashed_password):
        return None

    return user


def create_user(
    username: str,
    password: str,
    full_name: Optional[str] = None,
    role: str = "engineer",
    is_active: bool = True,
    db: Optional[Session] = None,
) -> User:
    """Create a new user with a bcrypt-hashed password and validated role."""
    clean_user = username.strip()
    clean_role = (role or "").strip().lower()
    if clean_role not in VALID_ROLES:
        raise ValueError(f"Invalid role: '{role}'. Must be one of {sorted(VALID_ROLES)}")

    hashed = hash_password(password)
    user = User(
        username=clean_user,
        hashed_password=hashed,
        full_name=full_name,
        role=clean_role,
        is_active=is_active,
    )

    if db is not None:
        db.add(user)
        db.commit()
        db.refresh(user)
        return user

    with SessionLocal() as session:
        session.add(user)
        session.commit()
        session.refresh(user)
        return user
