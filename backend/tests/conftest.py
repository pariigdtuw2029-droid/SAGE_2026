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

# JWT auth must be configured for the API-contract tests to reach the data
# endpoints; fixed test credentials keep behaviour deterministic and don't
# depend on a developer's local .env.
os.environ.setdefault("SAGE_JWT_SECRET", "test-secret-not-used-in-production-0123456789abcdef")
os.environ.setdefault("SAGE_JWT_EXPIRE_MINUTES", "30")
os.environ.setdefault("SAGE_AUTH_USERNAME", "admin")
os.environ.setdefault("SAGE_AUTH_PASSWORD", "test-password-123")

from app.main import app
from app import security


@pytest.fixture()
def client():
    """TestClient with a valid bearer token pre-attached to every request.

    The API-contract tests (lots/components/upload/...) exercise endpoint
    behaviour, not the auth guard — those live in test_auth.py, which uses
    its own unauthenticated requests.
    """
    c = TestClient(app)
    c.headers.update(
        {"Authorization": f"Bearer {security.create_access_token(subject='admin')}"}
    )
    return c
