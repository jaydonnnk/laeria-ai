"""Request authentication — Supabase Auth JWT validation.

Single-user mode: a request is valid only when it carries a Bearer token for
THE owner user (OWNER_USER_ID). The token is validated against Supabase Auth
(network call per request — fine at personal scale; swap for local JWT
verification against the project's JWKS if it ever matters).

Open endpoints: /health (liveness) and /vendor/* (x402 paid resources —
payment is their auth).
"""

from __future__ import annotations

from fastapi import Header, HTTPException

from core.config import get_settings
from core.logging import get_logger

logger = get_logger(__name__)


def require_owner(authorization: str = Header(default="")) -> str:
    """FastAPI dependency: validate the Bearer token and require it to belong
    to the configured owner. Returns the user id."""
    settings = get_settings()
    if not settings.owner_user_id:
        raise HTTPException(status_code=500, detail="OWNER_USER_ID not configured")

    token = authorization.removeprefix("Bearer ").strip()
    if not token:
        raise HTTPException(status_code=401, detail="missing bearer token")

    from db.client import get_supabase

    try:
        res = get_supabase().auth.get_user(token)
        user = res.user
    except Exception as exc:  # noqa: BLE001
        logger.info("token validation failed: %s", exc)
        raise HTTPException(status_code=401, detail="invalid or expired token") from exc

    if user is None or user.id != settings.owner_user_id:
        raise HTTPException(status_code=403, detail="not the owner")
    return user.id
