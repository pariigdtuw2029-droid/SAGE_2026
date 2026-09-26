import os
import tempfile

import pytest
from fastapi.testclient import TestClient

# Keep the API-contract tests hermetic: they exercise the service layer's
# mock-data fallback, so point Member 2's database layer at an empty SQLite
# file (no tables → connection errors → services fall back to demo data).
_fd, _db_path = tempfile.mkstemp(suffix=".db")
os.close(_fd)
os.environ["DATABASE_URL"] = f"sqlite:///{_db_path}"

# Test-only environment override for JWT signing secret
if "SAGE_JWT_SECRET" not in os.environ:
    os.environ["SAGE_JWT_SECRET"] = "sage-test-suite-secure-jwt-signing-secret-key-32bytes"

os.environ.setdefault("SAGE_JWT_EXPIRE_MINUTES", "30")
os.environ.setdefault("SAGE_ACCESS_TOKEN_EXPIRE_MINUTES", "1800")
os.environ.setdefault(
    "SAGE_USERS",
    "admin:test-password-123:admin,engineer:eng-test-pass:engineer,reviewer:rev-test-pass:reviewer",
)

from app.main import app
from app import security
from app.services import auth_service



@pytest.fixture()
def client():
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

