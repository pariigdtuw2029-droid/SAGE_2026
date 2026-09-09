# ASTRA-GUARD — SIH 2026

AI-assisted multi-parameter anomaly detection and risk-prioritisation
system. It flags unusual component behaviour beyond simple PASS/FAIL
threshold checks, to help engineers investigate potential risks earlier.

This is a **decision-support / early-screening tool**. It does not
replace qualification testing, certification, engineering judgement,
human review, or existing safety procedures.

```
CSV/test data → FastAPI → validation → database → analysis/ML
              → risk/explanation → FastAPI → frontend dashboard
```

## Backend architecture

```
backend/
├── app/
│   ├── main.py                 # FastAPI app, CORS, router registration, error handling
│   ├── api/                    # Routers — HTTP layer only
│   │   ├── health.py
│   │   ├── upload.py
│   │   ├── lots.py
│   │   ├── components.py
│   │   └── alerts.py
│   ├── schemas/                 # Pydantic request/response models
│   │   ├── upload.py
│   │   ├── lot.py
│   │   ├── component.py
│   │   ├── alert.py
│   │   └── risk.py
│   ├── services/                 # Business logic + the DB integration boundary
│   │   ├── upload_service.py     # File validation (type/empty/size)
│   │   ├── lot_service.py
│   │   ├── component_service.py
│   │   ├── alert_service.py
│   │   └── mock_data.py          # DEMO DATA ONLY — see note below
│   └── __init__.py
├── tests/
├── requirements.txt
└── .env.example
```

**Layering:** routers (`app/api`) only handle HTTP concerns — parsing
the request, calling a service function, returning a response with the
right status code. All logic lives in `app/services`. This means
swapping demo data for a real database only requires editing the
service files, not the routers.

## Member responsibilities

**Member 1 (this backend)** owns: the FastAPI app, routers, endpoints,
Pydantic schemas, upload validation, HTTP status codes, error handling,
CORS, environment configuration, OpenAPI/Swagger docs, and API tests.

**Member 2** owns: PostgreSQL, SQLAlchemy models, CRUD, data
preprocessing/cleaning, unit standardisation, and report data prep.

### Member 2 integration point

Every service function in `app/services/*_service.py` currently reads
from `app/services/mock_data.py`, a clearly isolated module of
placeholder data. To connect PostgreSQL:

1. Add SQLAlchemy models and a DB session dependency.
2. Replace the body of each function in `lot_service.py`,
   `component_service.py`, and `alert_service.py` with real queries.
3. Leave the function signatures and return shapes (dicts matching the
   Pydantic schemas) the same — the routers and API contract don't
   need to change.

No production values are hardcoded anywhere outside `mock_data.py`.

## Setup

```bash
cd backend
python -m venv venv

# Windows
venv\Scripts\activate
# macOS/Linux
source venv/bin/activate

pip install -r requirements.txt
```

Copy `.env.example` to `.env` and adjust if needed (optional for local
dev — sensible defaults are baked in):

```bash
cp .env.example .env
```

## Running the API

```bash
uvicorn app.main:app --reload
```

- API root: http://127.0.0.1:8000/
- Swagger UI: http://127.0.0.1:8000/docs
- ReDoc: http://127.0.0.1:8000/redoc

## Endpoints

| Method | Path | Description |
|---|---|---|
| GET | `/` | API status |
| GET | `/health` | Health check |
| POST | `/api/burnin/upload` | Upload a burn-in CSV |
| GET | `/api/lots` | List all lots |
| GET | `/api/lots/{lot_id}` | Get one lot |
| GET | `/api/lots/{lot_id}/summary` | Dashboard summary for a lot |
| GET | `/api/components/{id}` | Get one component |
| GET | `/api/components/{id}/trajectory` | Chart-ready actual/predicted trajectory |
| GET | `/api/components/{id}/report` | Explainability report (decision + reasons) |
| GET | `/api/alerts` | High-risk components requiring attention |

### Example: upload

```bash
curl -X POST http://127.0.0.1:8000/api/burnin/upload \
  -F "file=@sample_burnin.csv;type=text/csv"
```

Success (200):
```json
{"message": "File received successfully", "filename": "sample_burnin.csv", "status": "success"}
```

Rejected (400):
```json
{"detail": "Unsupported file type. Only .csv files are accepted."}
```

### Example: component report

```bash
curl http://127.0.0.1:8000/api/components/C104/report
```

```json
{
  "component_id": "C104",
  "decision": "REJECT",
  "risk_score": 87.0,
  "reasons": [
    "Strong deviation from lot baseline",
    "High early drift",
    "Predicted trajectory crosses safety envelope"
  ]
}
```

## Error codes

| Code | Meaning |
|---|---|
| 200 | Success |
| 400 | Bad request (invalid upload: wrong type / empty / too large) |
| 404 | Resource not found (missing lot / component) |
| 422 | Request validation error (e.g. missing required field) |
| 500 | Internal server error (logged server-side, never leaks details) |

## Environment variables

| Variable | Purpose | Default |
|---|---|---|
| `ALLOWED_ORIGINS` | Comma-separated list of origins allowed by CORS | `http://localhost:3000,http://127.0.0.1:3000,http://localhost:5173` |
| `DATABASE_URL` | Reserved for Member 2's PostgreSQL connection | not yet used |

Never commit a real `.env` file — it's git-ignored. Use `.env.example`
as the template.

## Testing

```bash
pip install -r requirements.txt   # includes pytest + httpx
pytest -v
```

19 tests cover: root/health, upload (success, wrong type, empty,
oversized, missing file), lots (list, get, missing → 404, summary),
components (get, missing → 404, trajectory, report), and alerts.

Tests do not require a live PostgreSQL database — they run entirely
against the mock service layer described above.

## Known limitations (by design, for this phase)

- All lot/component/alert data is demo data (`app/services/mock_data.py`),
  clearly labeled as such. It will be replaced once Member 2's
  PostgreSQL layer is ready.
- `python-multipart` is required for the upload endpoint — it's in
  `requirements.txt`.
