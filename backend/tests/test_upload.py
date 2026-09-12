def test_upload_success(client):
    response = client.post(
        "/api/burnin/upload",
        files={"file": ("data.csv", b"a,b\n1,2\n", "text/csv")},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "success"
    assert body["filename"] == "data.csv"
    assert "valid_rows" in body
    assert "lots_created" in body
    assert "components_created" in body


def test_upload_valid_burnin_dataset_returns_ingestion_counts(client):
    import io
    import os
    import pandas as pd

    data_csv = os.path.join(os.path.dirname(__file__), "..", "data", "burn_in_dataset.csv")
    if not os.path.exists(data_csv):
        return

    df = pd.read_csv(data_csv).head(60)
    buf = io.StringIO()
    df.to_csv(buf, index=False)
    csv_bytes = buf.getvalue().encode("utf-8")

    response = client.post(
        "/api/burnin/upload",
        files={"file": ("burn_in_sample.csv", csv_bytes, "text/csv")},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "success"
    assert body["valid_rows"] is not None and body["valid_rows"] > 0
    assert body["lots_created"] is not None and body["lots_created"] > 0
    assert body["components_created"] is not None and body["components_created"] > 0
    assert body["predictions_created"] is not None and body["predictions_created"] > 0
    assert body["risk_assessments_created"] is not None and body["risk_assessments_created"] > 0
    assert body["model_version"] is not None
    assert isinstance(body["warnings"], list)


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


def test_upload_whitespace_filename(client):
    response = client.post(
        "/api/burnin/upload",
        files={"file": ("   ", b"a,b\n1,2\n", "text/csv")},
    )
    assert response.status_code == 400
    assert "filename" in response.json()["detail"].lower()


def test_upload_dot_csv_only_filename(client):
    response = client.post(
        "/api/burnin/upload",
        files={"file": (".csv", b"a,b\n1,2\n", "text/csv")},
    )
    assert response.status_code == 400


def test_upload_whitespace_only_content(client):
    response = client.post(
        "/api/burnin/upload",
        files={"file": ("data.csv", b"   \n\r\n   ", "text/csv")},
    )
    assert response.status_code == 400
    assert "empty" in response.json()["detail"].lower()
