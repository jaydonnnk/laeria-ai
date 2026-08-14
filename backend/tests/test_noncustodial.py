"""Non-custodial settlement: the agent spends the user's funds via an
allowance, and the one real-money path is gated and capped.

The user keeps their keys; the operator (the X402_AGENT key) signs a
transferFrom that can only move what the user approved. Mainnet is refused
unless explicitly enabled, and even then only up to a hard cap — three
independent ceilings (allowance, mandate, this cap) guard a connected wallet.
"""
from __future__ import annotations

import pytest
from eth_account import Account

from core import current_user
from core.config import get_settings
from services import wallet as W

OPERATOR_KEY = "0x" + "11" * 32
OWNER = "0x" + "aa" * 20
MERCHANT = "0x" + "bb" * 20
GUEST = "guest-uuid-9999"
OWNER_UID = "owner-uuid-0000"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("X402_NETWORK", "eip155:43113")  # Fuji testnet
    monkeypatch.setenv("X402_AGENT_PRIVATE_KEY", OPERATOR_KEY)
    monkeypatch.setenv("X402_TREASURY_ADDRESS", "0x" + "cc" * 20)
    monkeypatch.setenv("OWNER_USER_ID", OWNER_UID)
    monkeypatch.delenv("WALLET_ENCRYPTION_KEY", raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _stub_rpc(monkeypatch, *, allowance_units: int = 50_000_000):
    """Route JSON-RPC: decimals=6, a fixed allowance, and a canned tx hash."""
    def fake(self, method, params):  # noqa: ANN001
        if method == "eth_getTransactionCount":
            return "0x0"
        if method == "eth_gasPrice":
            return hex(10**8)
        if method == "eth_sendRawTransaction":
            return "0xfeed"
        if method == "eth_call":
            data = params[0]["data"]
            if data == W._ERC20_DECIMALS_SELECTOR:
                return hex(6)
            if data.startswith(W._ERC20_ALLOWANCE_SELECTOR):
                return hex(allowance_units)
            return "0x0"
        return "0x0"

    monkeypatch.setattr(W.WalletService, "_rpc", fake)


# ---- operator + allowance reads ----

def test_operator_address_is_the_agent_keys_address():
    assert W.WalletService().operator_address() == Account.from_key(OPERATOR_KEY).address


def test_allowance_reads_from_the_chain(monkeypatch):
    _stub_rpc(monkeypatch, allowance_units=50_000_000)  # 50.00 at 6 decimals
    assert W.WalletService().allowance(OWNER) == 50.0


# ---- transferFrom (testnet happy path) ----

def test_transfer_from_signs_with_the_operator_and_moves_the_owners_funds(monkeypatch):
    _stub_rpc(monkeypatch)
    out = W.WalletService()._transfer_from(OWNER, MERCHANT, 10.0)
    assert out["tx_hash"] == "0xfeed"
    assert out["from"] == OWNER
    assert out["to"] == MERCHANT
    assert out["operator"] == Account.from_key(OPERATOR_KEY).address
    assert out["amount_usd"] == 10.0


# ---- mainnet gate + cap ----

def _go_mainnet(monkeypatch):
    monkeypatch.setenv("X402_NETWORK", "eip155:43114")  # Avalanche C-Chain
    get_settings.cache_clear()


def test_mainnet_settlement_refused_unless_enabled(monkeypatch):
    _go_mainnet(monkeypatch)
    with pytest.raises(W.WalletError, match="disabled"):
        W.WalletService()._transfer_from(OWNER, MERCHANT, 1.0)


def test_mainnet_settlement_refused_over_the_cap(monkeypatch):
    _go_mainnet(monkeypatch)
    monkeypatch.setenv("ALLOW_MAINNET_SETTLEMENT", "true")
    monkeypatch.setenv("MAX_SETTLEMENT_USD", "5")
    get_settings.cache_clear()
    with pytest.raises(W.WalletError, match="cap"):
        W.WalletService()._transfer_from(OWNER, MERCHANT, 10.0)


# ---- settle routes custodial vs connected ----

def test_settle_uses_transfer_from_for_a_connected_wallet(monkeypatch):
    """A bound non-owner with a connected (keyless) wallet settles via
    transferFrom, not a direct transfer."""
    import db.repositories as repo

    monkeypatch.setattr(repo, "get_user_wallet", lambda: {"address": OWNER, "key_encrypted": ""})

    seen: dict = {}

    def fake_tf(self, owner, to_address, amount, nonce=None):  # noqa: ANN001
        seen.update(owner=owner, to=to_address, amount=amount)
        return {"tx_hash": "0xtf", "explorer_url": "", "amount_usd": amount,
                "token_symbol": "XSGD", "from": owner, "to": to_address,
                "operator": "0xop", "network": "Avalanche Fuji (testnet)"}

    def boom_transfer(self, *a, **k):  # noqa: ANN001
        raise AssertionError("custodial _transfer must not run for a connected wallet")

    monkeypatch.setattr(W.WalletService, "_transfer_from", fake_tf)
    monkeypatch.setattr(W.WalletService, "_transfer", boom_transfer)

    token = current_user.set_current_user(GUEST)
    try:
        out = W.WalletService().settle_purchase(12.5)
    finally:
        current_user.reset_current_user(token)

    assert out["tx_hash"] == "0xtf"
    assert seen == {"owner": OWNER, "to": "0x" + "cc" * 20, "amount": 12.5}
