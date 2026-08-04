"""Auth-cache behaviour, especially the ways it must NOT weaken auth.

The cache exists to remove a Supabase round trip from every request. That is
only acceptable if it can never make an invalid token look valid, and never
outlive the token it was derived from.
"""

from __future__ import annotations

import base64
import json
import time

import pytest
from fastapi import HTTPException

from core import auth


def make_token(exp_offset: float = 3600.0, sub: str = "owner-uuid") -> str:
    """A JWT-shaped string with a real `exp`. Signature is irrelevant here —
    validation is Supabase's job; this only exercises our cache bounds."""
    header = base64.urlsafe_b64encode(b'{"alg":"HS256"}').decode().rstrip("=")
    body = json.dumps({"sub": sub, "exp": time.time() + exp_offset})
    payload = base64.urlsafe_b64encode(body.encode()).decode().rstrip("=")
    return f"{header}.{payload}.signature"


class FakeUser:
    def __init__(self, uid: str) -> None:
        self.id = uid


class FakeAuth:
    def __init__(self, uid: str) -> None:
        self.uid = uid
        self.calls = 0

    def get_user(self, token: str):
        self.calls += 1
        return type("Res", (), {"user": FakeUser(self.uid)})()


class FakeSupabase:
    def __init__(self, uid: str) -> None:
        self.auth = FakeAuth(uid)


@pytest.fixture(autouse=True)
def clean():
    auth.clear_auth_cache()
    yield
    auth.clear_auth_cache()


@pytest.fixture
def wired(monkeypatch):
    """Point require_owner at a fake Supabase and a known owner id."""
    owner = "owner-uuid"
    fake = FakeSupabase(owner)

    import db.client

    monkeypatch.setattr(db.client, "get_supabase", lambda: fake)

    settings = auth.get_settings()
    monkeypatch.setattr(settings, "owner_user_id", owner, raising=False)
    monkeypatch.setattr(settings, "auth_cache_seconds", 300, raising=False)
    monkeypatch.setattr(auth, "get_settings", lambda: settings)
    return fake, settings


def test_second_call_is_served_from_cache(wired):
    fake, _ = wired
    tok = make_token()
    assert auth.require_owner(f"Bearer {tok}") == "owner-uuid"
    assert auth.require_owner(f"Bearer {tok}") == "owner-uuid"
    assert fake.auth.calls == 1, "second request should not re-hit Supabase"


def test_different_tokens_are_cached_separately(wired):
    fake, _ = wired
    auth.require_owner(f"Bearer {make_token(sub='a')}")
    auth.require_owner(f"Bearer {make_token(sub='b')}")
    assert fake.auth.calls == 2


def test_cache_never_outlives_the_token(wired):
    """A token expiring in 2s must not be trusted for the full 300s TTL."""
    fake, _ = wired
    tok = make_token(exp_offset=2.0)
    auth.require_owner(f"Bearer {tok}")
    key = list(auth._cache.keys())[0]
    _, expires_at = auth._cache[key]
    assert expires_at - time.monotonic() <= 2.5, "TTL must clamp to token exp"


def test_already_expired_token_is_not_cached(wired):
    fake, _ = wired
    tok = make_token(exp_offset=-1.0)
    auth.require_owner(f"Bearer {tok}")
    assert not auth._cache, "an expired token must never enter the cache"


def test_rejection_is_not_cached(wired, monkeypatch):
    """A token for the wrong user must be re-checked, never remembered."""
    fake, settings = wired
    fake.auth.uid = "someone-else"
    tok = make_token()
    with pytest.raises(HTTPException) as e1:
        auth.require_owner(f"Bearer {tok}")
    assert e1.value.status_code == 403
    assert not auth._cache

    # once the same token resolves to the owner, it works — proving the
    # earlier failure left nothing poisoned behind
    fake.auth.uid = "owner-uuid"
    assert auth.require_owner(f"Bearer {tok}") == "owner-uuid"


def test_validation_error_is_not_cached(wired):
    fake, _ = wired

    def boom(token):
        raise RuntimeError("supabase down")

    fake.auth.get_user = boom
    with pytest.raises(HTTPException) as e:
        auth.require_owner(f"Bearer {make_token()}")
    assert e.value.status_code == 401
    assert not auth._cache


def test_missing_token_still_401(wired):
    with pytest.raises(HTTPException) as e:
        auth.require_owner("")
    assert e.value.status_code == 401


def test_ttl_zero_disables_caching(wired):
    fake, settings = wired
    settings.auth_cache_seconds = 0
    tok = make_token()
    auth.require_owner(f"Bearer {tok}")
    auth.require_owner(f"Bearer {tok}")
    assert fake.auth.calls == 2
    assert not auth._cache


def test_cache_is_bounded(wired):
    for i in range(auth._MAX_ENTRIES + 5):
        auth.require_owner(f"Bearer {make_token(sub=f'u{i}')}")
    assert len(auth._cache) <= auth._MAX_ENTRIES
