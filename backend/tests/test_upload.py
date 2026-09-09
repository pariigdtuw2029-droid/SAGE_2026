def test_upload_success(client):
    response = client.post(
        "/api/burnin/upload",
        files={"file": ("data.csv", b"a,b\n1,2\n", "text/csv")},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "success"
    assert body["filename"] == "data.csv"


def test_upload_wrong_file_type(client):
    response = client.post(
        "/api/burnin/upload",
        files={"file": ("data.txt", b"a,b\n1,2\n", "text/plain")},
    )
    assert response.status_code == 400
    assert "csv" in response.json()["detail"].lower()


def test_upload_empty_file(client):
    response = client.post(
        "/api/burnin/upload",
        files={"file": ("data.csv", b"", "text/csv")},
    )
    assert response.status_code == 400
    assert "empty" in response.json()["detail"].lower()


def test_upload_oversized_file(client):
    from app.services.upload_service import MAX_UPLOAD_SIZE_BYTES

    too_big = b"0" * (MAX_UPLOAD_SIZE_BYTES + 1)
    response = client.post(
        "/api/burnin/upload",
        files={"file": ("data.csv", too_big, "text/csv")},
    )
    assert response.status_code == 400
    assert "exceeds" in response.json()["detail"].lower()


def test_upload_missing_file(client):
    response = client.post("/api/burnin/upload")
    assert response.status_code == 422
