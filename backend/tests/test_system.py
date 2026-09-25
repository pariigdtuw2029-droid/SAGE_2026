from unittest.mock import patch

from app.schemas.system import SystemStatusResponse
from database import crud


def test_system_status_route_exists(client):
    """Test 1: GET /api/system/status returns 200 and valid JSON."""
    response = client.get("/api/system/status")
    assert response.status_code == 200
    data = response.json()
    assert "status" in data
    assert "database" in data
    assert "model_stack" in data


def test_system_status_response_contract(client):
    """Test 2: Response matches SystemStatusResponse Pydantic contract."""
    response = client.get("/api/system/status")
    assert response.status_code == 200
    payload = response.json()

    # Validate against Pydantic schema
    validated = SystemStatusResponse.model_validate(payload)
    assert validated.status in {"healthy", "degraded", "unavailable"}
    assert isinstance(validated.database.connected, bool)

    if validated.database.records is not None:
        rec = validated.database.records
        assert rec.lots >= 0
        assert rec.components >= 0
        assert rec.measurements >= 0
        assert rec.predictions >= 0
        assert rec.risk_assessments >= 0
        assert rec.explanations >= 0

    assert validated.model_stack.status in {"ready", "degraded", "unavailable"}
    assert validated.model_stack.module_a in {"loaded", "unavailable"}
    assert validated.model_stack.module_b in {"loaded", "unavailable"}
    assert validated.model_stack.module_c in {"active", "unavailable"}


def test_system_status_strictly_read_only(client):
    """Test 3: Calling GET /api/system/status does not mutate database records."""
    with crud.session_scope() as s:
        counts_before = crud.count_rows(db=s)

    # Call endpoint multiple times
    for _ in range(3):
        res = client.get("/api/system/status")
        assert res.status_code == 200

    with crud.session_scope() as s:
        counts_after = crud.count_rows(db=s)

    assert counts_before == counts_after


def test_existing_endpoints_unaffected(client):
    """Test 4: Existing API endpoints remain completely unaffected."""
    # Health probe
    health_res = client.get("/health")
    assert health_res.status_code == 200
    assert health_res.json() == {"status": "healthy"}

    # Root route
    root_res = client.get("/")
    assert root_res.status_code == 200
    assert root_res.json()["message"] == "SAGE API is running"

    # Lots endpoint
    lots_res = client.get("/api/lots")
    assert lots_res.status_code == 200
    assert isinstance(lots_res.json(), list)


def test_system_status_openapi_registration(client):
    """Test 5: /api/system/status is documented in /openapi.json."""
    openapi_res = client.get("/openapi.json")
    assert openapi_res.status_code == 200
    spec = openapi_res.json()

    assert "/api/system/status" in spec["paths"]
    op = spec["paths"]["/api/system/status"]["get"]
    assert "System" in op["tags"]
    assert "200" in op["responses"]


def test_system_status_no_sensitive_info_disclosure(client):
    """Test 6: Sanitization check ensures no sensitive paths, internals, or weights are leaked."""
    response = client.get("/api/system/status")
    assert response.status_code == 200
    raw_text = response.text.lower()

    # Must never disclose filesystem paths, credentials, joblib filenames, or algorithm weights
    forbidden_tokens = [
        "c:\\",
        "d:\\",
        "models\\",
        "models/",
        ".joblib",
        ".db",
        "secret",
        "password",
        "weight_anomaly",
        "weight_predicted_drift",
        "safety_slope",
        "traceback",
        "sqlalchemy",
    ]
    for token in forbidden_tokens:
        assert token not in raw_text, f"Forbidden sensitive token '{token}' exposed in response!"


def test_system_status_database_failure_resilience(client, monkeypatch):
    """Test 7: Controlled degradation when database connectivity fails."""
    def broken_count_rows(*args, **kwargs):
        raise RuntimeError("Simulated database failure")

    monkeypatch.setattr(crud, "count_rows", broken_count_rows)

    response = client.get("/api/system/status")
    assert response.status_code == 200
    data = response.json()
    assert data["database"]["connected"] is False
    assert data["database"]["records"] is None
    assert data["status"] in {"degraded", "unavailable"}
    # Ensure no exception traceback leaked
    assert "simulated database failure" not in response.text.lower()


def test_system_status_model_failure_resilience(client, monkeypatch):
    """Test 8: Controlled degradation when describe_stack fails."""
    import app.api.system as system_module

    def broken_describe_stack(*args, **kwargs):
        raise RuntimeError("Simulated model descriptor failure")

    monkeypatch.setattr(system_module, "describe_stack", broken_describe_stack)

    response = client.get("/api/system/status")
    assert response.status_code == 200
    data = response.json()
    assert data["model_stack"]["status"] == "unavailable"
    assert data["model_stack"]["module_a"] == "unavailable"
    assert data["status"] in {"degraded", "unavailable"}
    assert "simulated model descriptor failure" not in response.text.lower()
