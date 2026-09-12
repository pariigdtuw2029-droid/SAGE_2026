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

from app.main import app


@pytest.fixture()
def client():
    return TestClient(app)
