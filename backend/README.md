# ASTRA-GUARD — FastAPI Backend (Member 1)

This directory contains the FastAPI application backend for the ASTRA-GUARD early-screening and anomaly detection system.

---

## Architecture & Responsibilities

Member 1 owns the HTTP application tier:
- **FastAPI application lifecycle, CORS, routing, and error handling**
- **Pydantic schemas and response contracts**
- **File upload validation (filetype, size, non-empty)**
- **OpenAPI / Swagger documentation**
- **HTTP unit and integration tests**

The backend is completely decoupled from model training and raw ML artifact generation:
- Model training, ML research, and joblib model file generation belong to the **ML Team**.
- PostgreSQL storage, ORM models, and batch ML inference ingestion belong to **Member 2** (`backend/database/`).
- The application backend (`backend/app/`) serves stable, structured response contracts to the frontend.

---

## API Endpoints

### Health Check
- `GET /` — Basic root service check
- `GET /health` — Health status check (`{"status": "healthy"}`)

### Burn-in Upload
- `POST /api/burnin/upload` — Accepts a burn-in test CSV file (`multipart/form-data`)
  - Validates `.csv` extension, non-empty filename, non-empty content, and max size (10 MB).
  - Returns `UploadResponse`: `{"message": "File received successfully", "filename": "...", "status": "success"}`

### Lots
- `GET /api/lots` — List all lots (`List[LotResponse]`)
- `GET /api/lots/{lot_id}` — Get single lot details (`LotResponse`)
- `GET /api/lots/{lot_id}/summary` — Get aggregated lot screening summary (`LotSummaryResponse`)

### Components
- `GET /api/components/{component_id}` — Single component screening snapshot (`ComponentResponse`)
- `GET /api/components/{component_id}/trajectory` — Actual vs predicted burn-in trajectory data (`TrajectoryResponse`)
- `GET /api/components/{component_id}/report` — Detailed component engineering report (`ComponentReportResponse`)

### Alerts
- `GET /api/alerts` — High-risk components requiring engineering review (`List[AlertResponse]`)

---

## ML Output Alignment Fields

To maintain alignment with the retrained Module A/B/C ML stack without creating hard dependencies on ML packages:

1. **`slope_reject_flag` (`Optional[bool]`):**
   - Available on `ComponentResponse` and `ComponentReportResponse`.
   - `True` indicates the component's 168h predicted drift rate exceeds the 99th percentile safety slope measured on the Safe population.
   - `None` or `False` if not flagged or unmeasured.

2. **`reliability_tier` (`Optional[str]`):**
   - Available on `ComponentResponse` and `ComponentReportResponse`.
   - String values: `"Space-Safe"`, `"Borderline"`, or `"High-Risk"`.

3. **SHAP Explanation Pass-Through (`reasons: List[str]`):**
   - On `ComponentReportResponse`, `reasons` treats explainability text as opaque strings.
   - Transparently surfaces both physics-based descriptions and `[SHAP]` attribution strings (e.g., `"[SHAP] Anomaly score driven mainly by Leakage_delta_24h(+0.04)"`).

---

## Error Format

All error responses consistently return JSON with a `detail` key:

```json
{
  "detail": "Error message description"
}
```

- Client errors (e.g. invalid IDs, unsupported file types, empty files) return HTTP `400 Bad Request` or `404 Not Found`.
- Unhandled server exceptions are caught by the global exception handler, logged with full tracebacks server-side, and return HTTP `500 Internal Server Error` with `{"detail": "Internal server error."}` to avoid leaking sensitive internal details.

---

## Local Testing

Run Member 1's backend test suite:

```bash
pytest tests/test_alerts.py tests/test_components.py tests/test_health.py tests/test_lots.py tests/test_upload.py
```

---

## Interactive Documentation

When the FastAPI server is running:
- **Swagger UI:** `http://localhost:8000/docs`
- **ReDoc:** `http://localhost:8000/redoc`
- **OpenAPI JSON:** `http://localhost:8000/openapi.json`
