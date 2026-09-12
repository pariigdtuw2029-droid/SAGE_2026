def test_get_existing_component(client):
    response = client.get("/api/components/C104")
    assert response.status_code == 200
    assert response.json()["component_id"] == "C104"


def test_get_missing_component_returns_404(client):
    response = client.get("/api/components/DOES_NOT_EXIST")
    assert response.status_code == 404


def test_get_trajectory(client):
    response = client.get("/api/components/C104/trajectory")
    assert response.status_code == 200
    body = response.json()
    assert body["component_id"] == "C104"
    assert "actual" in body and "predicted" in body


def test_get_trajectory_missing_component_returns_404(client):
    response = client.get("/api/components/DOES_NOT_EXIST/trajectory")
    assert response.status_code == 404


def test_get_report(client):
    response = client.get("/api/components/C104/report")
    assert response.status_code == 200
    body = response.json()
    assert body["decision"] == "REJECT"
    assert isinstance(body["reasons"], list) and len(body["reasons"]) > 0


def test_get_report_missing_component_returns_404(client):
    response = client.get("/api/components/DOES_NOT_EXIST/report")
    assert response.status_code == 404


def test_get_component_ml_output_fields(client):
    response = client.get("/api/components/C104")
    assert response.status_code == 200
    body = response.json()
    assert body["slope_reject_flag"] is True
    assert body["reliability_tier"] == "High-Risk"
    assert body["drift_score"] is not None
    assert body["reliability_index"] is not None
    assert body["model_version"] == "sage-1.1"
    assert body["split"] == "test"

    safe_resp = client.get("/api/components/C101")
    assert safe_resp.status_code == 200
    safe_body = safe_resp.json()
    assert safe_body["slope_reject_flag"] is False
    assert safe_body["reliability_tier"] == "Space-Safe"
    assert safe_body["drift_score"] is not None
    assert safe_body["reliability_index"] is not None
    assert safe_body["model_version"] == "sage-1.1"
    assert safe_body["split"] == "train"


def test_get_report_ml_output_fields_and_shap_reasons(client):
    response = client.get("/api/components/C104/report")
    assert response.status_code == 200
    body = response.json()
    assert body["slope_reject_flag"] is True
    assert body["reliability_tier"] == "High-Risk"
    assert body["drift_score"] is not None
    assert body["predicted_drift_score"] is not None
    assert body["reliability_index"] is not None
    assert body["lot_relative_score"] is not None
    assert body["multivariate_score"] is not None
    assert body["worst_lot_zscore"] is not None
    assert body["absolute_spec_fail"] is not None
    assert body["interval_low"] is not None
    assert body["interval_high"] is not None
    assert body["uncertainty_score"] is not None
    assert any("[SHAP]" in r for r in body["reasons"])
    assert isinstance(body["explanations"], list)
    assert len(body["explanations"]) > 0
    assert "feature" in body["explanations"][0]
    assert "reason" in body["explanations"][0]


def test_real_database_component_ml_fields():
    """Verify real database component C504 against astra_guard.db if it exists."""
    import os
    from database.connection import DEFAULT_SQLITE_URL
    from database import crud
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    db_path = DEFAULT_SQLITE_URL.replace("sqlite:///", "")
    if not os.path.exists(db_path):
        return

    engine = create_engine(DEFAULT_SQLITE_URL)
    Session = sessionmaker(bind=engine)
    db = Session()
    try:
        comp = crud.get_component("C504", db=db)
        assert comp is not None
        assert comp["reliability_index"] is not None
        assert comp["split"] == "train"

        rep = crud.get_component_report("C504", db=db)
        assert rep is not None
        assert rep["reliability_index"] is not None
        assert len(rep["explanations"]) > 0
    finally:
        db.close()


def test_get_component_invalid_id_returns_400(client):
    response = client.get("/api/components/%20")
    assert response.status_code == 400
    assert "invalid" in response.json()["detail"].lower()
