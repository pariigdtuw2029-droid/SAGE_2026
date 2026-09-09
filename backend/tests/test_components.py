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
