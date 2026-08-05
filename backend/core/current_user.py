"""Who the current request belongs to.

Every repository query filters on a user id, and until now that id was a
constant — OWNER_USER_ID — which is what made the backend single-tenant
despite a schema that was multi-tenant from the start.

The id now travels with the request. It is bound by pure ASGI middleware, and
that detail is load-bearing rather than stylistic:

    A ContextVar set inside a FastAPI dependency never reaches the endpoint.
    Sync dependencies and sync endpoints are each dispatched through the
    threadpool, and every dispatch copies the context — so a value set in the
    dependency's copy is discarded before the handler runs. Verified: the
    endpoint reads the default. Resetting is worse still, raising "Token was
    created in a different Context".

Pure ASGI middleware runs in the request's own context, before any threadpool
dispatch, so the copies handed to the dependency and the endpoint both inherit
the value. BaseHTTPMiddleware does NOT work here either — Starlette dispatches
it in a separate context.

Outside a request — the monitor worker, scripts, tests — nothing is bound, so
it falls back to OWNER_USER_ID and behaves exactly as before. That fallback is
not a security decision: nothing outside a request is reachable by a user.
"""

from __future__ import annotations

from contextvars import ContextVar

from core.config import get_settings

_current_user: ContextVar[str | None] = ContextVar("current_user_id", default=None)


def set_current_user(user_id: str | None):
    """Bind the request's user. Returns the token needed to reset it."""
    return _current_user.set(user_id)


def reset_current_user(token) -> None:
    _current_user.reset(token)


def current_user_id() -> str:
    """The signed-in user for this request, or the owner outside one."""
    uid = _current_user.get()
    if uid:
        return uid

    owner = get_settings().owner_user_id
    if not owner:
        raise RuntimeError(
            "No authenticated user and OWNER_USER_ID is not set in .env — "
            "create a user in the Supabase dashboard (Authentication -> Users) "
            "and paste its UUID."
        )
    return owner


def is_owner() -> bool:
    return current_user_id() == get_settings().owner_user_id


class CurrentUserMiddleware:
    """Binds the caller for the whole request, and unbinds it afterwards.

    Deliberately raw ASGI rather than @app.middleware / BaseHTTPMiddleware —
    see the module docstring. Validation failures are NOT raised here: this
    only binds what it can read, and the route dependencies remain the thing
    that decides whether a request is allowed. An unauthenticated request
    simply leaves nothing bound.
    """

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        from core.auth import peek_user_id

        header = ""
        for k, v in scope.get("headers") or []:
            if k == b"authorization":
                header = v.decode("latin-1")
                break

        token = set_current_user(peek_user_id(header))
        try:
            await self.app(scope, receive, send)
        finally:
            reset_current_user(token)
