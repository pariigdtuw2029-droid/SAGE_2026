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

from app.core.security import create_access_token
from app.main import app
from app.services.auth_service import init_auth_db
from app.services.audit_service import init_audit_db
from app.services.review_service import init_reviews_db


@pytest.fixture()
def client():
    init_auth_db()
    init_audit_db()
    init_reviews_db()
    token = create_access_token(subject="admin")
    c = TestClient(app)
    c.headers.update({"Authorization": f"Bearer {token}"})
    return c

