"""FastAPI application entrypoint.

Run locally:
    uvicorn api.main:app --reload --host 0.0.0.0 --port 8000

Routes are wired but return 501 until their phase implements them.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.routes import actions, monitor, obsidian, research
from core.config import get_settings
from core.logging import configure_logging

configure_logging()
settings = get_settings()

app = FastAPI(
    title="baryon.ai",
    version="0.1.0",
    description="Reddit-as-human-signal research and monitoring.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tighten in Phase 5
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(research.router)
app.include_router(monitor.router)
app.include_router(obsidian.router)
app.include_router(actions.router)


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "env": settings.app_env, "version": "0.1.0"}
