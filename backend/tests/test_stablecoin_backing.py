"""The card must be backed by the wallet.

Before this check existed, the funding rail and the card rail shared nothing
but a mandate object: an agent wallet holding zero would still get a card
issued against the mandate caps alone. That is an unbacked credit line wearing
a stablecoin costume, and it is the one thing this design is supposed not to
be.

The direction of every failure below is deliberate. An unreadable balance is
zero allowance, not unlimited — the same rule ActionMandate applies to unset
caps.
"""
from __future__ import annotations

import pytest

from api.routes.actions import _stablecoin_backing
from services import wallet as W
from services.payment import MandateViolation


def _patch_wallet(monkeypatch, payload: dict | None = None, boom: Exception | None = None):
    class _Fake:
        def __init__(self) -> None:
            if boom is not None:
                raise boom

        def balances(self) -> dict:
            return payload or {}

    monkeypatch.setattr(W, "WalletService", _Fake)


def _balances(token: float, symbol: str = "XSGD", error: str = "") -> dict:
    agent = {"address": "0x" + "11" * 20, "error": error} if error else {
        "address": "0x" + "11" * 20, "token": token, "native": 0.1
    }
    return {"token_symbol": symbol, "agent": agent, "treasury": {}}


def test_a_funded_wallet_returns_its_balance_as_the_ceiling(monkeypatch):
    _patch_wallet(monkeypatch, _balances(100.0))
    assert _stablecoin_backing(32.95) == 100.0


def test_exact_balance_is_enough(monkeypatch):
    """Boundary: holding precisely the purchase amount is sufficient. Float
    noise must not turn a funded wallet into a refusal."""
    _patch_wallet(monkeypatch, _balances(32.95))
    assert _stablecoin_backing(32.95) == 32.95


def test_a_short_wallet_refuses_and_names_both_numbers(monkeypatch):
    """The real case: 19.94 held against a 32.95 order — previously issued a
    46.20 card anyway."""
    _patch_wallet(monkeypatch, _balances(19.94))
    with pytest.raises(MandateViolation) as exc:
        _stablecoin_backing(32.95)
    msg = str(exc.value)
    assert "19.94" in msg and "32.95" in msg and "XSGD" in msg


def test_an_empty_wallet_refuses(monkeypatch):
    _patch_wallet(monkeypatch, _balances(0.0))
    with pytest.raises(MandateViolation):
        _stablecoin_backing(0.01)


def test_an_unreadable_balance_refuses_rather_than_assuming_funds(monkeypatch):
    """RPC returned, but for this address it carries an error instead of a
    number. Absence of a balance is not permission to spend."""
    _patch_wallet(monkeypatch, _balances(0.0, error="execution reverted"))
    with pytest.raises(MandateViolation, match="cannot read"):
        _stablecoin_backing(10.0)


def test_an_unreachable_chain_refuses_rather_than_assuming_funds(monkeypatch):
    """The RPC itself is down. Fail closed — a network outage must not read as
    an unlimited wallet."""
    _patch_wallet(monkeypatch, boom=RuntimeError("rpc unreachable"))
    with pytest.raises(MandateViolation, match="cannot read"):
        _stablecoin_backing(10.0)


def test_the_refusal_is_a_mandate_violation_so_the_action_is_cancelled(monkeypatch):
    """Type matters: _execute_card_purchase maps MandateViolation to a
    *cancelled* action (a considered refusal), not a *failed* one (a crash).
    An underfunded wallet is the former."""
    _patch_wallet(monkeypatch, _balances(1.0))
    with pytest.raises(MandateViolation):
        _stablecoin_backing(50.0)
