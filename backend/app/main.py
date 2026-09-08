from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.health import router as health_router


app = FastAPI(
    title="ASTRA-GUARD API",
    description="Backend API for the ASTRA-GUARD anomaly detection system",
    version="1.0.0"
)


# CORS configuration
# Allows the frontend to communicate with the backend during development.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
def root():
    return {
        "message": "ASTRA-GUARD API is running"
    }


# Health check route
app.include_router(health_router)