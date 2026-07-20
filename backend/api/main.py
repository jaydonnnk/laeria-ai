"""FastAPI application entrypoint.

Run locally:
    uvicorn api.main:app --reload --host 0.0.0.0 --port 8000

Auth: every feature router requires a Supabase Auth bearer token belonging to
the owner (see core/auth.py). /health and /vendor/* stay open — the vendor
endpoints are x402 paid resources whose payment IS their access control.
"""

from __future__ import annotations

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.routes import actions, cards, monitor, obsidian, research, store, vendor, wallet
from core.auth import require_owner
from core.config import get_settings
from core.logging import configure_logging

configure_logging()
settings = get_settings()

app = FastAPI(
    title="baryon.ai",
    version="0.1.0",
    description="Reddit-as-human-signal research and monitoring.",
)

# Bearer-token auth (not cookies), so a wildcard origin is acceptable in dev;
# set CORS_ORIGINS to the Vercel domain in production.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins.split(",") if settings.cors_origins else ["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

_authed = [Depends(require_owner)]
app.include_router(research.router, dependencies=_authed)
app.include_router(monitor.router, dependencies=_authed)
app.include_router(obsidian.router, dependencies=_authed)
app.include_router(actions.router, dependencies=_authed)
app.include_router(store.router, dependencies=_authed)
app.include_router(cards.router, dependencies=_authed)
app.include_router(wallet.router, dependencies=_authed)
app.include_router(vendor.router)  # x402: payment is the auth


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "env": settings.app_env, "version": "0.1.0"}


@app.get("/usage", dependencies=_authed)
def usage() -> dict:
    """Resource consumption counters (Phase 5): Reddit requests, LLM
    calls/tokens, embedding batches, executed payment volume."""
    from core.usage import snapshot

    return snapshot()
