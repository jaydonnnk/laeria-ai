"""On-chain settlement of a completed card purchase.

This is the leg that makes the funded stablecoin actually pay for something.
Two properties matter more than the happy path:

* It never raises. Settlement runs after the order exists, so a chain failure
  is a field on the receipt, not an exception that would relabel a bought item
  as a crash.
* It refuses mainnet, like every other transfer in wallet.py, because the
  refusal lives in the one shared `_transfer` rather than being remembered
  separately per caller.
"""
from __future__ import annotations

import pytest

from api.routes.actions import _settle_on_chain
from core.config import get_settings
from services import wallet as W

MERCHANT = "0x" + "ab" * 20
TREASURY = "0x" + "cd" * 20
AGENT_KEY = "0x" + "11" * 32


@pytest.fixture(autouse=True)
def _base_env(monkeypatch):
    monkeypatch.setenv("X402_NETWORK", "eip155:43113")
    monkeypatch.setenv("X402_AGENT_PRIVATE_KEY", AGENT_KEY)
    monkeypatch.setenv("X402_TREASURY_ADDRESS", TREASURY)
    monkeypatch.delenv("MERCHANT_SETTLEMENT_ADDRESS", raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _capture_transfer(monkeypatch) -> dict:
    """Replace the signing/broadcast layer, keeping the routing logic under
    test."""
    seen: dict = {}

    def fake(self, private_key, to_address, amount):  # noqa: ANN001
        seen.update(key=private_key, to=to_address, amount=amount)
        return {
            "tx_hash": "0xdeadbeef",
            "explorer_url": "https://testnet.snowtrace.io/tx/0xdeadbeef",
            "amount_usd": round(amount, 2),
            "token_symbol": "XSGD",
            "from": "0x" + "11" * 20,
            "to": to_address,
            "network": "Avalanche Fuji (testnet)",
        }

    monkeypatch.setattr(W.WalletService, "_transfer", fake)
    return seen


# ---- routing ----

def test_settlement_pays_the_configured_merchant(monkeypatch):
    monkeypatch.setenv("MERCHANT_SETTLEMENT_ADDRESS", MERCHANT)
    get_settings.cache_clear()
    seen = _capture_transfer(monkeypatch)

    W.WalletService().settle_purchase(32.95)
    assert seen["to"] == MERCHANT
    assert seen["amount"] == 32.95


def test_settlement_falls_back_to_the_treasury(monkeypatch):
    """Unset merchant address is not an error — the treasury is the demo's
    merchant and is already the x402 vendor's payTo."""
    seen = _capture_transfer(monkeypatch)

    W.WalletService().settle_purchase(10.0)
    assert seen["to"] == TREASURY


def test_settlement_is_signed_by_the_agent_not_the_treasury(monkeypatch):
    """The agent pays for its own purchase. Signing with the treasury key
    would settle from the wrong wallet and leave the backing check meaning
    nothing."""
    seen = _capture_transfer(monkeypatch)

    W.WalletService().settle_purchase(10.0)
    assert seen["key"] == AGENT_KEY


def test_settlement_without_any_wallet_is_refused(monkeypatch):
    """No custodial key and no connected address — nothing to settle from."""
    monkeypatch.setenv("X402_AGENT_PRIVATE_KEY", "")
    monkeypatch.setenv("X402_AGENT_ADDRESS", "")
    get_settings.cache_clear()
    with pytest.raises(W.WalletError, match="no wallet to settle from"):
        W.WalletService().settle_purchase(10.0)


def test_settlement_without_any_destination_is_refused(monkeypatch):
    monkeypatch.setenv("X402_TREASURY_ADDRESS", "")
    get_settings.cache_clear()
    with pytest.raises(W.WalletError, match="settlement destination"):
        W.WalletService().settle_purchase(10.0)


# ---- the mainnet refusal is shared, not per-caller ----

def test_settlement_refuses_mainnet_the_same_way_funding_does(monkeypatch):
    monkeypatch.setenv("X402_NETWORK", "eip155:43114")  # Avalanche C-Chain
    monkeypatch.setenv("STABLECOIN_CONTRACT", "0x" + "ee" * 20)
    get_settings.cache_clear()
    with pytest.raises(W.WalletError, match="human act"):
        W.WalletService().settle_purchase(10.0)


def test_funding_refuses_mainnet_through_the_same_guard(monkeypatch):
    monkeypatch.setenv("X402_NETWORK", "eip155:43114")
    monkeypatch.setenv("STABLECOIN_CONTRACT", "0x" + "ee" * 20)
    monkeypatch.setenv("X402_TREASURY_PRIVATE_KEY", "0x" + "22" * 32)
    monkeypatch.setenv("X402_AGENT_ADDRESS", "0x" + "33" * 20)
    get_settings.cache_clear()
    with pytest.raises(W.WalletError, match="human act"):
        W.WalletService().fund_agent(10.0)


# ---- the caller never raises ----

def test_a_successful_settlement_is_reported_on_the_receipt(monkeypatch):
    _capture_transfer(monkeypatch)
    out = _settle_on_chain(32.95)
    assert out["settled"] is True
    assert out["tx_hash"] == "0xdeadbeef"
    assert out["explorer_url"].endswith("0xdeadbeef")


def test_a_failed_settlement_is_recorded_rather_than_raised(monkeypatch):
    """The order already exists. A chain failure must not turn a completed
    purchase into an exception — it becomes an honest "ordered, not settled"
    on the receipt."""
    def boom(self, *a, **kw):  # noqa: ANN001, ANN002
        raise RuntimeError("insufficient funds for gas")

    monkeypatch.setattr(W.WalletService, "_transfer", boom)

    out = _settle_on_chain(32.95)
    assert out["settled"] is False
    assert "gas" in out["error"]
    assert out["amount_usd"] == 32.95


def test_an_unusable_wallet_config_still_does_not_raise(monkeypatch):
    """Even a wallet that cannot be constructed at all (bad network) must
    degrade to an unsettled receipt."""
    monkeypatch.setenv("X402_NETWORK", "eip155:99999")
    get_settings.cache_clear()

    out = _settle_on_chain(5.0)
    assert out["settled"] is False
    assert out["error"]
