"""Tenancy boundary: who a request runs as, and who may spend.

These drive the real ASGI app through TestClient rather than calling
dependencies directly. That distinction caught a real bug: an earlier version
bound the user inside a FastAPI dependency, which unit tests happily confirmed
— but a ContextVar set in a dependency never reaches the endpoint, because
each threadpool dispatch copies the context. Every request would have silently
scoped to the owner. Calling the pieces in isolation cannot see that; a real
request can.

Two properties matter:

1. Repository queries scope to the SIGNED-IN user, not a constant.
2. Anything that spends stays owner-only. The agent wallet key, the Stripe
   cardholder and the storefront session are single global instances, so data
   scoping cannot separate them — only the auth level can.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core import auth, current_user

OWNER = "owner-uuid-0000"
GUEST = "guest-uuid-1111"


class FakeAuth:
    """Treats the bearer token as the user id, so a test picks who is calling."""

    def get_user(self, token: str):
        if token in (OWNER, GUEST):
            return type("Res", (), {"user": type("U", (), {"id": token})()})()
        raise RuntimeError("bad token")


class FakeSupabase:
    def __init__(self) -> None:
        self.auth = FakeAuth()


@pytest.fixture(autouse=True)
def wired(monkeypatch):
    auth.clear_auth_cache()
    import db.client

    monkeypatch.setattr(db.client, "get_supabase", lambda: FakeSupabase())
    settings = auth.get_settings()
    monkeypatch.setattr(settings, "owner_user_id", OWNER, raising=False)
    monkeypatch.setattr(settings, "auth_cache_seconds", 0, raising=False)
    monkeypatch.setattr(auth, "get_settings", lambda: settings)
    monkeypatch.setattr(current_user, "get_settings", lambda: settings)
    yield
    auth.clear_auth_cache()


@pytest.fixture
def probe():
    """A miniature app wired exactly like the real one, exposing what a
    repository query would have scoped to."""
    from fastapi import Depends

    app = FastAPI()
    app.add_middleware(current_user.CurrentUserMiddleware)

    @app.get("/as-user", dependencies=[Depends(auth.require_user)])
    def as_user() -> dict:
        from db import repositories as repo

        return {"scoped_to": repo._owner()}

    @app.get("/as-owner", dependencies=[Depends(auth.require_owner)])
    def as_owner() -> dict:
        from db import repositories as repo

        return {"scoped_to": repo._owner()}

    return TestClient(app)


def get(client, path: str, uid: str | None):
    headers = {"Authorization": f"Bearer {uid}"} if uid else {}
    return client.get(path, headers=headers)


# ---- property 1: queries scope to the caller ----

def test_guest_request_scopes_to_the_guest(probe):
    r = get(probe, "/as-user", GUEST)
    assert r.status_code == 200
    assert r.json()["scoped_to"] == GUEST, (
        "a guest's queries read as the owner — every account would share one "
        "dataset"
    )


def test_owner_request_scopes_to_the_owner(probe):
    assert get(probe, "/as-user", OWNER).json()["scoped_to"] == OWNER


def test_identity_does_not_leak_between_requests(probe):
    """A finished request must leave nothing bound. Otherwise the next caller
    inherits the previous one's identity."""
    assert get(probe, "/as-user", GUEST).json()["scoped_to"] == GUEST
    assert get(probe, "/as-user", OWNER).json()["scoped_to"] == OWNER
    assert get(probe, "/as-user", GUEST).json()["scoped_to"] == GUEST


def test_outside_a_request_falls_back_to_owner():
    """The monitor worker and scripts run unbound and must keep behaving
    exactly as they did before multi-user."""
    from db import repositories as repo

    assert repo._owner() == OWNER


# ---- property 2: spending is owner-only ----

def test_guest_is_refused_owner_endpoints(probe):
    r = get(probe, "/as-owner", GUEST)
    assert r.status_code == 403
    assert "owner" in r.json()["detail"].lower()


def test_owner_passes_owner_endpoints(probe):
    assert get(probe, "/as-owner", OWNER).status_code == 200


def test_unauthenticated_is_401_not_403(probe):
    """A signed-out visitor should be told to sign in, not that they are the
    wrong person — the frontend bounces to /login on 401."""
    for path in ("/as-user", "/as-owner"):
        assert get(probe, path, None).status_code == 401


def test_bad_token_is_401(probe):
    assert get(probe, "/as-user", "not-a-real-token").status_code == 401


# ---- the money endpoints on the REAL app are wired to the owner dependency ----

def test_spending_routers_require_owner():
    """Guards against a future router being added to the wrong list."""
    from api.main import app

    owner_only = {"/actions", "/cards", "/wallet", "/store", "/obsidian"}
    seen: dict[str, set] = {}
    for route in app.routes:
        path = getattr(route, "path", "")
        prefix = "/" + path.lstrip("/").split("/")[0] if path.strip("/") else ""
        if prefix not in owner_only:
            continue
        names = {
            getattr(d.dependency, "__name__", "")
            for d in getattr(route, "dependencies", [])
        }
        seen.setdefault(prefix, set()).update(names)

    for prefix in owner_only:
        assert prefix in seen, f"{prefix} routes not found"
        assert "require_owner" in seen[prefix], (
            f"{prefix} is not owner-gated — a guest could reach the shared "
            "wallet, cardholder or storefront"
        )


def test_research_act_is_owner_gated():
    """The one research endpoint that spends."""
    from api.main import app

    for route in app.routes:
        if getattr(route, "path", "") == "/research/act":
            names = {
                getattr(d.dependency, "__name__", "")
                for d in getattr(route, "dependencies", [])
            }
            assert "require_owner" in names, "/research/act spends and must be owner-only"
            return
    pytest.fail("/research/act not found")
