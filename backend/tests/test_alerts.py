def test_list_alerts(client):
    response = client.get("/api/alerts")
    assert response.status_code == 200
    body = response.json()
    assert isinstance(body, list)
    for alert in body:
        assert alert["risk_score"] >= 50
        assert alert["severity"] in {"MEDIUM", "HIGH", "CRITICAL"}
