def test_list_lots(client):
    response = client.get("/api/lots")
    assert response.status_code == 200
    body = response.json()
    assert isinstance(body, list)
    assert len(body) > 0
    assert "lot_id" in body[0]


def test_get_existing_lot(client):
    response = client.get("/api/lots/LOT_27")
    assert response.status_code == 200
    assert response.json()["lot_id"] == "LOT_27"


def test_get_missing_lot_returns_404(client):
    response = client.get("/api/lots/DOES_NOT_EXIST")
    assert response.status_code == 404


def test_get_lot_summary(client):
    response = client.get("/api/lots/LOT_27/summary")
    assert response.status_code == 200
    body = response.json()
    assert body["lot_id"] == "LOT_27"
    assert "average_anomaly_score" in body


def test_get_missing_lot_summary_returns_404(client):
    response = client.get("/api/lots/DOES_NOT_EXIST/summary")
    assert response.status_code == 404


def test_get_lot_invalid_id_returns_400(client):
    response = client.get("/api/lots/%20")
    assert response.status_code == 400
    assert "invalid" in response.json()["detail"].lower()


def test_get_lot_summary_invalid_id_returns_400(client):
    response = client.get("/api/lots/%20/summary")
    assert response.status_code == 400
    assert "invalid" in response.json()["detail"].lower()


def test_get_lot_components_mock(client):
    response = client.get("/api/lots/LOT_27/components")
    assert response.status_code == 200
    body = response.json()
    assert isinstance(body, list)
    assert len(body) == 4
    comp_ids = [c["component_id"] for c in body]
    assert "C101" in comp_ids
    assert "C104" in comp_ids


def test_get_missing_lot_components_returns_404(client):
    response = client.get("/api/lots/DOES_NOT_EXIST/components")
    assert response.status_code == 404


def test_get_lot_components_invalid_id_returns_400(client):
    response = client.get("/api/lots/%20/components")
    assert response.status_code == 400
    assert "invalid" in response.json()["detail"].lower()


def test_real_database_lot_components():
    """Verify real lots against astra_guard.db if it exists."""
    from database.connection import DEFAULT_SQLITE_URL
    from database import crud
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    import os

    db_path = DEFAULT_SQLITE_URL.replace("sqlite:///", "")
    if not os.path.exists(db_path):
        return

    engine = create_engine(DEFAULT_SQLITE_URL)
    Session = sessionmaker(bind=engine)
    db = Session()
    try:
        # 1. ISRO-SCL-250113-02 -> C504
        comps_1 = crud.get_lot_components("ISRO-SCL-250113-02", db=db)
        c_ids_1 = [c["component_id"] for c in comps_1]
        assert "C504" in c_ids_1
        c504 = next(c for c in comps_1 if c["component_id"] == "C504")
        assert c504["lot_id"] == "ISRO-SCL-250113-02"
        assert c504["decision"] == "HOLD"
        assert c504["risk_level"] == "MEDIUM"
        assert "C501" not in c_ids_1  # exact lot filter check

        # 2. ISRO-SCL-250421-16 -> C501
        comps_2 = crud.get_lot_components("ISRO-SCL-250421-16", db=db)
        c_ids_2 = [c["component_id"] for c in comps_2]
        assert "C501" in c_ids_2
        c501 = next(c for c in comps_2 if c["component_id"] == "C501")
        assert c501["lot_id"] == "ISRO-SCL-250421-16"
        assert c501["decision"] == "PASS"
        assert "C504" not in c_ids_2  # exact lot filter check
    finally:
        db.close()
