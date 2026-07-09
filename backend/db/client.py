"""Supabase client singleton.

Uses the service-role key — backend only. Never import this into anything
that runs client-side. Row Level Security still applies via user_id scoping
in queries; the service role bypasses RLS, so query scoping is the backend's
responsibility.
"""

from __future__ import annotations

from functools import lru_cache

from supabase import Client, create_client

from core.config import get_settings
from core.logging import get_logger

logger = get_logger(__name__)


@lru_cache
def get_supabase() -> Client:
    settings = get_settings()
    return create_client(settings.supabase_url, settings.supabase_key)


def healthcheck() -> bool:
    """Confirm the client can reach the project. Used by test_environment."""
    try:
        client = get_supabase()
        # A trivial call that requires a valid connection + key.
        client.auth.get_session()
        return True
    except Exception as exc:  # noqa: BLE001
        logger.error("Supabase healthcheck failed: %s", exc)
        return False
