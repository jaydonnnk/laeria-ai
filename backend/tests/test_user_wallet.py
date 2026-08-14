"""Per-user custodial wallet provisioning — the new load-bearing paths.

Three things must hold, and none of them touch the chain:

  * the stored private key is encrypted at rest and round-trips exactly;
  * the owner and any pre-003 config (no WALLET_ENCRYPTION_KEY) stay on the
    single env wallet, so the existing deployment and every offline caller
    behave as before;
  * a brand-new account is generated, stored and funded exactly once, and the
    wallet the agent later signs with is the one that was stored.

The chain leg (_fund_new_wallet) is stubbed — it is the one part that needs a
node, and it is exercised on Fuji by hand, not here.
"""
from __future__ import annotations

import pytest
from cryptography.fernet import Fernet
from eth_account import Account

from core import current_user
from core.config import get_settings
from services import wallet as W

ENC_KEY = Fernet.generate_key().decode()
OWNER = "owner-uuid-0000"
GUEST = "guest-uuid-1111"
ENV_ADDR = "0x" + "aa" * 20
ENV_KEY = "0x" + "11" * 32


@pytest.fixture(autouse=True)
def _settings(monkeypatch):
    monkeypatch.setenv("X402_NETWORK", "eip155:43113")
    monkeypatch.setenv("X402_AGENT_ADDRESS", ENV_ADDR)
    monkeypatch.setenv("X402_AGENT_PRIVATE_KEY", ENV_KEY)
    monkeypatch.setenv("X402_TREASURY_ADDRESS", "0x" + "22" * 20)
    monkeypatch.setenv("OWNER_USER_ID", OWNER)
    # Start with provisioning OFF; individual tests opt in.
    monkeypatch.delenv("WALLET_ENCRYPTION_KEY", raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _enable(monkeypatch):
    monkeypatch.setenv("WALLET_ENCRYPTION_KEY", ENC_KEY)
    get_settings.cache_clear()


def _as(uid: str):
    """Bind the current user for one test body (synchronous, same context)."""
    return current_user.set_current_user(uid)


def test_encrypt_decrypt_round_trip(monkeypatch):
    _enable(monkeypatch)
    secret = "0x" + "ab" * 32
    token = W._encrypt_key(secret)
    assert token != secret, "the key must not be stored in the clear"
    assert W._decrypt_key(token) == secret


def test_provisioning_disabled_stays_on_env_wallet():
    """No WALLET_ENCRYPTION_KEY -> the whole module behaves pre-003: one env
    wallet for everyone, and resolution never consults current_user or the DB."""
    out = W.ensure_user_wallet()
    assert out["provisioned"] is False
    assert out["address"] == ENV_ADDR

    addr, key = W.WalletService()._resolve_agent()
    assert addr == ENV_ADDR
    assert key == ENV_KEY


def test_owner_keeps_env_wallet(monkeypatch):
    """Provisioning on, but the owner is pinned to the env wallet and the DB is
    never consulted for them."""
    _enable(monkeypatch)
    import db.repositories as repo

    def _boom():
        raise AssertionError("owner path must not read the wallet table")

    monkeypatch.setattr(repo, "get_user_wallet", _boom)

    token = _as(OWNER)
    try:
        out = W.ensure_user_wallet()
    finally:
        current_user.reset_current_user(token)

    assert out["provisioned"] is False
    assert out["address"] == ENV_ADDR


def test_new_user_is_generated_stored_and_funded_once(monkeypatch):
    _enable(monkeypatch)
    import db.repositories as repo

    store: dict = {}
    monkeypatch.setattr(repo, "get_user_wallet", lambda: store.get("w"))

    def fake_set(address, key_encrypted):
        store["w"] = {"address": address, "key_encrypted": key_encrypted}
        return store["w"]

    monkeypatch.setattr(repo, "set_user_wallet", fake_set)

    funded = {"n": 0}

    def fake_fund(address):
        funded["n"] += 1
        return {"funded": True, "gas_tx": "0xgas", "xsgd_tx": "0xxsgd"}

    monkeypatch.setattr(W, "_fund_new_wallet", fake_fund)

    token = _as(GUEST)
    try:
        out = W.ensure_user_wallet()
        assert out["provisioned"] is True
        assert out["funded"] is True
        assert funded["n"] == 1
        addr = out["address"]

        # The stored key is encrypted and belongs to the generated address.
        stored = store["w"]
        assert stored["address"] == addr
        assert stored["key_encrypted"] != ""
        priv = W._decrypt_key(stored["key_encrypted"])
        assert Account.from_key(priv).address == addr

        # Resolution returns that same wallet, key decrypted, ready to sign.
        r_addr, r_key = W.WalletService()._resolve_agent()
        assert r_addr == addr
        assert Account.from_key(r_key).address == addr

        # Idempotent: a second visit reuses the wallet and never re-funds.
        again = W.ensure_user_wallet()
        assert again["provisioned"] is True
        assert funded["n"] == 1
    finally:
        current_user.reset_current_user(token)


def test_two_users_get_distinct_wallets(monkeypatch):
    """The whole point: two accounts must not resolve to the same wallet."""
    _enable(monkeypatch)
    import db.repositories as repo

    rows: dict[str, dict] = {}
    monkeypatch.setattr(W, "_fund_new_wallet", lambda address: {"funded": True})
    monkeypatch.setattr(
        repo, "get_user_wallet", lambda: rows.get(current_user.current_user_id())
    )
    monkeypatch.setattr(
        repo,
        "set_user_wallet",
        lambda address, key_encrypted: rows.__setitem__(
            current_user.current_user_id(),
            {"address": address, "key_encrypted": key_encrypted},
        ),
    )

    addrs = set()
    for uid in (GUEST, "another-guest-2222"):
        token = _as(uid)
        try:
            addrs.add(W.ensure_user_wallet()["address"])
        finally:
            current_user.reset_current_user(token)

    assert len(addrs) == 2, "two accounts collapsed onto one wallet"
