"""FastAPI application entrypoint.

Run locally:
    uvicorn api.main:app --reload --host 0.0.0.0 --port 8000

Auth: every feature router requires a Supabase Auth bearer token (see
core/auth.py). Most require only a signed-in account — each user has their own
custodial wallet, data and cards — while the Obsidian vault stays owner-only as
the one shared local instance. /health and /vendor/* stay open — the vendor
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

# Any signed-in account: per-user data, nothing shared. The payment routers
# moved here once each user got their own custodial agent wallet (migration
# 003 + services/wallet.ensure_user_wallet): the wallet is provisioned and
# funded per account, cards and actions are already user-scoped rows, and the
# storefront is a shared read-only catalogue. So a second account acting on
# these spends its OWN wallet, not the owner's — which is exactly the property
# require_owner used to stand in for.
_user = [Depends(require_user)]
app.include_router(research.router, dependencies=_user)
app.include_router(monitor.router, dependencies=_user)
app.include_router(actions.router, dependencies=_user)
app.include_router(store.router, dependencies=_user)
app.include_router(cards.router, dependencies=_user)
app.include_router(wallet.router, dependencies=_user)

# Owner only: the Obsidian vault is the one genuinely single, local instance —
# it lives on the owner's machine and no per-user equivalent exists — so it
# stays gated to the deployment owner.
_owner = [Depends(require_owner)]
app.include_router(obsidian.router, dependencies=_owner)

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
