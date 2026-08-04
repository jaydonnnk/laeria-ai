"""Request authentication — Supabase Auth JWT validation.

Single-user mode: a request is valid only when it carries a Bearer token for
THE owner user (OWNER_USER_ID).

Validation hits Supabase Auth, which costs a full network round trip. Measured
against the deployed backend that put every API call at 1.3-3.7s before any
actual work began, and each page fires several in parallel — so a successful
validation is cached for a short window. The cache can only ever SHORTEN a
token's life, never extend it: entries expire at the earlier of the configured
TTL and the token's own `exp` claim, and failures are never cached.

Open endpoints: /health (liveness) and /vendor/* (x402 paid resources —
payment is their auth).
"""

from __future__ import annotations

import base64
import hashlib
import json
import threading
import time

from fastapi import Header, HTTPException

from core.config import get_settings
from core.logging import get_logger

logger = get_logger(__name__)

# token sha256 -> (user_id, expires_at_monotonic)
_cache: dict[str, tuple[str, float]] = {}
_cache_lock = threading.Lock()
# Single-user build; this exists so a token-rotation loop can't grow the dict
# without bound.
_MAX_ENTRIES = 64


def _token_exp(token: str) -> float | None:
    """Read `exp` out of a JWT payload without verifying the signature.

    Only ever used to shorten cache lifetime, so an attacker-controlled value
    cannot extend validity — a forged token still fails validation upstream at
    Supabase before anything reaches the cache.
    """
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return float(json.loads(base64.urlsafe_b64decode(payload))["exp"])
    except Exception:  # noqa: BLE001
        return None


def _cache_get(key: str) -> str | None:
    with _cache_lock:
        hit = _cache.get(key)
        if hit is None:
            return None
        user_id, expires_at = hit
        if time.monotonic() >= expires_at:
            _cache.pop(key, None)
            return None
        return user_id


def _cache_put(key: str, user_id: str, ttl: float) -> None:
    if ttl <= 0:
        return
    with _cache_lock:
        if len(_cache) >= _MAX_ENTRIES:
            _cache.clear()
        _cache[key] = (user_id, time.monotonic() + ttl)


def clear_auth_cache() -> None:
    """Drop every cached validation. Used by tests."""
    with _cache_lock:
        _cache.clear()


def require_owner(authorization: str = Header(default="")) -> str:
    """FastAPI dependency: validate the Bearer token and require it to belong
    to the configured owner. Returns the user id."""
    settings = get_settings()
    if not settings.owner_user_id:
        raise HTTPException(status_code=500, detail="OWNER_USER_ID not configured")

    token = authorization.removeprefix("Bearer ").strip()
    if not token:
        raise HTTPException(status_code=401, detail="missing bearer token")

    key = hashlib.sha256(token.encode()).hexdigest()
    cached = _cache_get(key)
    if cached is not None:
        return cached

    from db.client import get_supabase

    try:
        res = get_supabase().auth.get_user(token)
        user = res.user
    except Exception as exc:  # noqa: BLE001
        logger.info("token validation failed: %s", exc)
        raise HTTPException(status_code=401, detail="invalid or expired token") from exc

    if user is None or user.id != settings.owner_user_id:
        raise HTTPException(status_code=403, detail="not the owner")

    # Never outlive the token itself.
    ttl = float(settings.auth_cache_seconds)
    exp = _token_exp(token)
    if exp is not None:
        ttl = min(ttl, max(exp - time.time(), 0.0))
    _cache_put(key, user.id, ttl)
    return user.id
