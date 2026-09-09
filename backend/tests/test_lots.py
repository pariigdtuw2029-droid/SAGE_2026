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
