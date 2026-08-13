"""CompanyVal AI — FastAPI application entrypoint."""
from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import get_settings
from .database import Base, engine
from . import models  # noqa: F401 — register all models
from .api import cases, documents, engine as engine_api, interview, settings_api

logging.basicConfig(level=logging.INFO)

settings = get_settings()

app = FastAPI(
    title="CompanyVal AI",
    description="AI-Assisted Business Valuation — Upload. Understand. Question. "
                "Simulate. Value.",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_url, "http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(cases.router, tags=["cases"])
app.include_router(documents.router, tags=["documents"])
app.include_router(interview.router, tags=["interview"])
app.include_router(engine_api.router, tags=["valuation"])
app.include_router(settings_api.router, tags=["settings"])


@app.on_event("startup")
def startup() -> None:
    # Alembic manages migrations in production; create_all covers local dev.
    Base.metadata.create_all(bind=engine)


@app.get("/api/health")
def health():
    return {"status": "ok", "app": settings.app_name}
