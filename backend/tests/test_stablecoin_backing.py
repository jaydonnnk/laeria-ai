"""The card must be backed by the wallet.

Before this check existed, the funding rail and the card rail shared nothing
but a mandate object: an agent wallet holding zero would still get a card
issued against the mandate caps alone. That is an unbacked credit line wearing
a stablecoin costume, and it is the one thing this design is supposed not to
be.

Two backing shapes now:
  * custodial — the wallet's own balance is the ceiling.
  * non-custodial — the agent spends the user's funds via an allowance, so the
    ceiling is min(balance, allowance): a card can outrun neither the funds nor
    what the user approved.

The direction of every failure below is deliberate. An unreadable balance or
allowance is zero, not unlimited — the same rule ActionMandate applies to unset
caps.
"""
from __future__ import annotations

import pytest

from api.routes.actions import _stablecoin_backing
from services import wallet as W
from services.payment import MandateViolation

_ADDR = "0x" + "11" * 20


def _patch_wallet(
    monkeypatch,
    *,
    address: str = _ADDR,
    custodial: bool = True,
    balance: float = 0.0,
    allowance: float = 0.0,
    symbol: str = "XSGD",
    ctor_boom: Exception | None = None,
    balance_boom: Exception | None = None,
    allowance_boom: Exception | None = None,
):
    class _Fake:
        def __init__(self) -> None:
            if ctor_boom is not None:
                raise ctor_boom
            self._net = {"token_symbol": symbol}

        def resolve_agent_wallet(self) -> dict:
            return {"address": address, "custodial": custodial}

        def token_balance(self, addr: str) -> float:
            if balance_boom is not None:
                raise balance_boom
            return balance

        def allowance(self, addr: str) -> float:
            if allowance_boom is not None:
                raise allowance_boom
            return allowance

    monkeypatch.setattr(W, "WalletService", _Fake)


# ---- custodial ----

def test_a_funded_wallet_returns_its_balance_as_the_ceiling(monkeypatch):
    _patch_wallet(monkeypatch, custodial=True, balance=100.0)
    assert _stablecoin_backing(32.95) == 100.0


def test_exact_balance_is_enough(monkeypatch):
    """Boundary: holding precisely the purchase amount is sufficient. Float
    noise must not turn a funded wallet into a refusal."""
    _patch_wallet(monkeypatch, custodial=True, balance=32.95)
    assert _stablecoin_backing(32.95) == 32.95


def test_a_short_wallet_refuses_and_names_both_numbers(monkeypatch):
    """The real case: 19.94 held against a 32.95 order — previously issued a
    46.20 card anyway."""
    _patch_wallet(monkeypatch, custodial=True, balance=19.94)
    with pytest.raises(MandateViolation) as exc:
        _stablecoin_backing(32.95)
    msg = str(exc.value)
    assert "19.94" in msg and "32.95" in msg and "XSGD" in msg


def test_an_empty_wallet_refuses(monkeypatch):
    _patch_wallet(monkeypatch, custodial=True, balance=0.0)
    with pytest.raises(MandateViolation):
        _stablecoin_backing(0.01)


def test_no_wallet_at_all_refuses(monkeypatch):
    """Neither a custodial nor a connected wallet — nothing can back a card."""
    _patch_wallet(monkeypatch, address="", custodial=False)
    with pytest.raises(MandateViolation, match="no wallet backs"):
        _stablecoin_backing(10.0)


def test_an_unreadable_balance_refuses_rather_than_assuming_funds(monkeypatch):
    """The balance read itself failed. Absence of a balance is not permission
    to spend."""
    _patch_wallet(monkeypatch, custodial=True, balance_boom=RuntimeError("reverted"))
    with pytest.raises(MandateViolation, match="cannot read"):
        _stablecoin_backing(10.0)


def test_an_unreachable_chain_refuses_rather_than_assuming_funds(monkeypatch):
    """The service could not even be constructed (bad RPC/config). Fail closed
    — a network outage must not read as an unlimited wallet."""
    _patch_wallet(monkeypatch, ctor_boom=RuntimeError("rpc unreachable"))
    with pytest.raises(MandateViolation, match="cannot resolve"):
        _stablecoin_backing(10.0)


def test_the_refusal_is_a_mandate_violation_so_the_action_is_cancelled(monkeypatch):
    """Type matters: _execute_card_purchase maps MandateViolation to a
    *cancelled* action (a considered refusal), not a *failed* one (a crash).
    An underfunded wallet is the former."""
    _patch_wallet(monkeypatch, custodial=True, balance=1.0)
    with pytest.raises(MandateViolation):
        _stablecoin_backing(50.0)


# ---- non-custodial: min(balance, allowance) ----

def test_non_custodial_ceiling_is_the_lesser_of_balance_and_allowance(monkeypatch):
    _patch_wallet(monkeypatch, custodial=False, balance=100.0, allowance=50.0)
    assert _stablecoin_backing(40.0) == 50.0


def test_non_custodial_balance_can_be_the_binding_limit(monkeypatch):
    _patch_wallet(monkeypatch, custodial=False, balance=30.0, allowance=90.0)
    assert _stablecoin_backing(25.0) == 30.0


def test_non_custodial_insufficient_allowance_refuses_and_says_approve(monkeypatch):
    """Funded wallet, but the user approved too little — the agent may not
    spend past the allowance no matter the balance."""
    _patch_wallet(monkeypatch, custodial=False, balance=100.0, allowance=20.0)
    with pytest.raises(MandateViolation, match="approve") as exc:
        _stablecoin_backing(40.0)
    assert "20" in str(exc.value)


def test_non_custodial_unreadable_allowance_refuses(monkeypatch):
    _patch_wallet(
        monkeypatch, custodial=False, balance=100.0,
        allowance_boom=RuntimeError("reverted"),
    )
    with pytest.raises(MandateViolation, match="allowance"):
        _stablecoin_backing(10.0)
