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
from core.auth import require_owner, require_user
from core.current_user import CurrentUserMiddleware
from core.config import get_settings
from core.logging import configure_logging

configure_logging()
settings = get_settings()

app = FastAPI(
    title="laeria.ai",
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

# Binds the caller so repository queries scope to them. Must be raw ASGI:
# a ContextVar set in a dependency never reaches the endpoint. See
# core.current_user for the full reasoning.
app.add_middleware(CurrentUserMiddleware)

# Any signed-in account: per-user data, nothing shared.
_user = [Depends(require_user)]
app.include_router(research.router, dependencies=_user)
app.include_router(monitor.router, dependencies=_user)

# Owner only: these touch the agent wallet key, the Stripe cardholder, the
# storefront session or the local vault — all single global instances, so a
# second account here would be spending the owner's money.
_owner = [Depends(require_owner)]
app.include_router(obsidian.router, dependencies=_owner)
app.include_router(actions.router, dependencies=_owner)
app.include_router(store.router, dependencies=_owner)
app.include_router(cards.router, dependencies=_owner)
app.include_router(wallet.router, dependencies=_owner)

app.include_router(vendor.router)  # x402: payment is the auth


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "env": settings.app_env, "version": "0.1.0"}


@app.get("/me")
def me(user_id: str = Depends(require_user)) -> dict:
    """Who the caller is, and whether they may use the payment features.

    The frontend cannot work `is_owner` out for itself — OWNER_USER_ID lives
    only in the backend env — and without it the nav would offer Commerce and
    Actions to accounts that get a 403 on arrival.
    """
    return {"user_id": user_id, "is_owner": user_id == settings.owner_user_id}


@app.get("/usage", dependencies=_user)
def usage() -> dict:
    """Resource consumption counters (Phase 5): Reddit requests, LLM
    calls/tokens, embedding batches, executed payment volume."""
    from core.usage import snapshot

    return snapshot()
