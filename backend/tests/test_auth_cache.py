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


def authed(header: str, dep=None):
    """require_user validates and returns the id; require_owner then decides."""
    uid = auth.require_user(header)
    return dep(uid) if dep else uid


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
    assert authed(f"Bearer {tok}") == "owner-uuid"
    assert authed(f"Bearer {tok}") == "owner-uuid"
    assert fake.auth.calls == 1, "second request should not re-hit Supabase"


def test_different_tokens_are_cached_separately(wired):
    fake, _ = wired
    authed(f"Bearer {make_token(sub='a')}")
    authed(f"Bearer {make_token(sub='b')}")
    assert fake.auth.calls == 2


def test_cache_never_outlives_the_token(wired):
    """A token expiring in 2s must not be trusted for the full 300s TTL."""
    fake, _ = wired
    tok = make_token(exp_offset=2.0)
    authed(f"Bearer {tok}")
    key = list(auth._cache.keys())[0]
    _, expires_at = auth._cache[key]
    assert expires_at - time.monotonic() <= 2.5, "TTL must clamp to token exp"


def test_already_expired_token_is_not_cached(wired):
    fake, _ = wired
    tok = make_token(exp_offset=-1.0)
    authed(f"Bearer {tok}")
    assert not auth._cache, "an expired token must never enter the cache"


def test_caching_validation_never_grants_authorization(wired):
    """The cache remembers WHO a token belongs to, never WHAT they may do.

    Since the require_user/require_owner split, validation is cached but
    authorization is decided per call. A non-owner's token is cached like
    anyone else's and must still be refused by require_owner every time —
    otherwise a cache hit would quietly become a permission grant.
    """
    fake, _ = wired
    fake.auth.uid = "someone-else"
    tok = make_token()

    for _ in range(3):
        with pytest.raises(HTTPException) as e:
            authed(f"Bearer {tok}", dep=auth.require_owner)
        assert e.value.status_code == 403

    # the same cached token is still valid for user-level access
    assert authed(f"Bearer {tok}") == "someone-else"


def test_validation_error_is_not_cached(wired):
    fake, _ = wired

    def boom(token):
        raise RuntimeError("supabase down")

    fake.auth.get_user = boom
    with pytest.raises(HTTPException) as e:
        authed(f"Bearer {make_token()}")
    assert e.value.status_code == 401
    assert not auth._cache


def test_missing_token_still_401(wired):
    with pytest.raises(HTTPException) as e:
        authed("")
    assert e.value.status_code == 401


def test_ttl_zero_disables_caching(wired):
    fake, settings = wired
    settings.auth_cache_seconds = 0
    tok = make_token()
    authed(f"Bearer {tok}")
    authed(f"Bearer {tok}")
    assert fake.auth.calls == 2
    assert not auth._cache


def test_cache_is_bounded(wired):
    for i in range(auth._MAX_ENTRIES + 5):
        authed(f"Bearer {make_token(sub=f'u{i}')}")
    assert len(auth._cache) <= auth._MAX_ENTRIES
