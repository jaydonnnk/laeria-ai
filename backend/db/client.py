"""Supabase client singleton.

Uses the service-role key — backend only. Never import this into anything
that runs client-side. Row Level Security still applies via user_id scoping
in queries; the service role bypasses RLS, so query scoping is the backend's
responsibility.

Connection reuse: postgrest keeps HTTP/2 connections alive in a pool. When one
sits idle long enough for Supabase to close its end, the next request on it
fails instantly with `RemoteProtocolError: Server disconnected` before any
response forms — which reaches the browser as a bare network failure with no
CORS headers, i.e. an error that looks nothing like its cause. This was the
source of the intermittent page failures seen locally and on Render.

Two mitigations, because neither alone is sufficient:
  * expire idle connections quickly, so the stale window is small
  * retry once on a dead connection, because the window can never be zero
"""

from __future__ import annotations

from functools import lru_cache

import httpx
from supabase import Client, ClientOptions, create_client

from core.config import get_settings
from core.logging import get_logger

logger = get_logger(__name__)

# Supabase closes idle keep-alive connections server-side. Expiring ours
# sooner than that means we rarely hand a dead socket to the next request.
_KEEPALIVE_EXPIRY_SECONDS = 5.0

# A connection that died before carrying a request — safe to replay, because
# the server never saw it.
_DEAD_CONNECTION_ERRORS = (
    httpx.RemoteProtocolError,
    httpx.ConnectError,
    httpx.ReadError,
    httpx.WriteError,
    httpx.PoolTimeout,
)


# Only replay methods with no side effects. "Server disconnected" can be
# raised while reading a response, meaning a write may already have been
# applied — replaying a POST could duplicate an action, a card, or a payment.
_REPLAYABLE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


class _RetryDeadConnections(httpx.BaseTransport):
    """Replay a read once when the pooled connection turned out to be dead.

    Sits under every Supabase call, so the 22 repository call sites stay
    untouched and no future one can forget to opt in.
    """

    def __init__(self, inner: httpx.BaseTransport) -> None:
        self._inner = inner

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        try:
            return self._inner.handle_request(request)
        except _DEAD_CONNECTION_ERRORS as exc:
            if request.method not in _REPLAYABLE_METHODS:
                raise
            logger.warning(
                "dead pooled connection on %s %s (%s) — replaying once",
                request.method,
                request.url.path,
                type(exc).__name__,
            )
            return self._inner.handle_request(request)

    def close(self) -> None:
        self._inner.close()


@lru_cache
def get_supabase() -> Client:
    settings = get_settings()
    return create_client(
        settings.supabase_url,
        settings.supabase_key,
        options=ClientOptions(
            httpx_client=httpx.Client(
                transport=_RetryDeadConnections(httpx.HTTPTransport(http2=True, retries=1)),
                timeout=httpx.Timeout(30.0, connect=10.0),
                limits=httpx.Limits(
                    max_connections=20,
                    max_keepalive_connections=5,
                    keepalive_expiry=_KEEPALIVE_EXPIRY_SECONDS,
                ),
            )
        ),
    )


def healthcheck() -> bool:
    """Confirm the client can reach the project and the schema is applied.

    Queries the `profiles` table (created by schema.sql). A valid connection +
    key returns rows or an empty list; a bad key/URL or missing table raises.
    Note: auth.get_session() is NOT used — it rejects the new sb_secret_ key
    format and only reads local session state anyway.
    """
    try:
        client = get_supabase()
        client.table("profiles").select("id").limit(1).execute()
        return True
    except Exception as exc:  # noqa: BLE001
        logger.error("Supabase healthcheck failed: %s", exc)
        return False
