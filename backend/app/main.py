import logging
import os
from pathlib import Path

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api.upload import router as upload_router
from app.api.health import router as health_router
from app.api.lots import router as lots_router
from app.api.components import router as components_router
from app.api.alerts import router as alerts_router

logger = logging.getLogger("sage")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("SAGE API startup complete (v1.0.0)")
    yield
    logger.info("SAGE API shutdown complete")


app = FastAPI(
    title="SAGE API",
    description="Backend API for the SAGE anomaly detection system",
    version="1.0.0",
    lifespan=lifespan,
)


# CORS configuration
# NOTE: allow_origins=["*"] together with allow_credentials=True is invalid
# per the CORS spec (browsers reject wildcard origin + credentials), so it
# was silently broken for any credentialed request. Origins are now read
# from an env var (comma-separated) with a sane local-dev default, and
# credentials are left off since no cookie/session auth exists yet.
ALLOWED_ORIGINS = os.getenv(
    "ALLOWED_ORIGINS",
    "http://localhost:3000,http://127.0.0.1:3000,http://localhost:5173,"
    "http://localhost:5500,http://127.0.0.1:5500,http://localhost:8080,http://127.0.0.1:8080",
).split(",")


app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/", tags=["Health"])
def root():
    return {
        "message": "SAGE API is running"
    }


# Health check route
app.include_router(health_router)

# Burn-in upload route
app.include_router(upload_router)

# Lot routes
app.include_router(lots_router)

# Component routes
app.include_router(components_router)

# Alerts route
app.include_router(alerts_router)


# --- Serve the Frontend from the same app (one public URL) ---
# The frontend folder is a sibling of backend/ inside the repo; on Render it
# lands at /opt/render/project/src/Frontend. Override with FRONTEND_DIR.
FRONTEND_DIR = Path(
    os.getenv(
        "FRONTEND_DIR",
        Path(__file__).resolve().parent.parent.parent / "Frontend",
    )
)


@app.get("/app", include_in_schema=False)
def frontend_app():
    """Shareable dashboard link (serves the same file as /index.html).

    Registered before the static mount below so it wins route matching.
    """
    index = FRONTEND_DIR / "index.html"
    if index.is_file():
        return FileResponse(index)
    raise HTTPException(status_code=404, detail="Frontend not deployed")


if (FRONTEND_DIR / "index.html").is_file():
    app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
    logger.info("Serving frontend from: %s", FRONTEND_DIR)
else:
    logger.warning(
        "Frontend not found at %s — API-only mode. "
        "Set FRONTEND_DIR to the folder containing index.html.",
        FRONTEND_DIR,
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    """
    Safety net: any exception not already handled by an endpoint's own
    HTTPException raises ends up here. We log the real error server-side
    but never return stack traces, file paths, or internals to the client.
    """
    logger.exception("Unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error."},
    )
