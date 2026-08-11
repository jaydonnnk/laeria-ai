"""Token decimals come from the token, not from a human typing an env var.

Every balance and every transfer amount in wallet.py is scaled by this number.
A wrong value does not fail — it misreports by orders of magnitude, which is
the worst kind of wrong for a money display. The contract knows the answer, so
it gets asked, and config is only the fallback for an unreachable RPC.
"""
from __future__ import annotations

import httpx
import pytest

from core.config import get_settings
from services import wallet as W

_DECIMALS_DATA = W._ERC20_DECIMALS_SELECTOR


class _Resp:
    def __init__(self, result: str) -> None:
        self._result = result

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return {"jsonrpc": "2.0", "id": 1, "result": self._result}


def _stub_rpc(monkeypatch, *, decimals_hex: str | None, balance_hex: str = "0x0"):
    """Route JSON-RPC by method/selector. decimals_hex=None makes the
    decimals() call fail the way an unreachable RPC does."""
    calls: list[str] = []

    def fake_post(url, json=None, timeout=None):  # noqa: ANN001, A002
        method = json["method"]
        if method == "eth_call":
            data = json["params"][0]["data"]
            if data == _DECIMALS_DATA:
                calls.append("decimals")
                if decimals_hex is None:
                    raise httpx.ConnectError("rpc down")
                return _Resp(decimals_hex)
            calls.append("balanceOf")
            return _Resp(balance_hex)
        calls.append(method)
        return _Resp("0x0")

    monkeypatch.setattr(W.httpx, "post", fake_post)
    return calls


@pytest.fixture(autouse=True)
def _clean_settings(monkeypatch):
    monkeypatch.setenv("X402_NETWORK", "eip155:84532")
    monkeypatch.setenv("X402_AGENT_ADDRESS", "0x" + "11" * 20)
    monkeypatch.setenv("X402_TREASURY_ADDRESS", "0x" + "22" * 20)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_the_contract_beats_the_configured_value(monkeypatch):
    """Config says 6 (the USDC default), the token says 18. 1e18 wins, so a
    balance of 1 token reads as 1.0 rather than a trillion."""
    monkeypatch.setenv("STABLECOIN_DECIMALS", "6")
    get_settings.cache_clear()
    _stub_rpc(monkeypatch, decimals_hex=hex(18), balance_hex=hex(10**18))

    svc = W.WalletService()
    assert svc._token_decimals == 18
    assert svc._token_balance("0x" + "11" * 20) == 1.0


def test_decimals_are_asked_once_and_then_cached(monkeypatch):
    calls = _stub_rpc(monkeypatch, decimals_hex=hex(6), balance_hex=hex(10**6))
    svc = W.WalletService()
    svc._token_balance("0x" + "11" * 20)
    svc._token_balance("0x" + "11" * 20)
    assert calls.count("decimals") == 1


def test_an_unreachable_rpc_falls_back_to_the_configured_value(monkeypatch):
    monkeypatch.setenv("STABLECOIN_DECIMALS", "8")
    get_settings.cache_clear()
    _stub_rpc(monkeypatch, decimals_hex=None)

    assert W.WalletService()._token_decimals == 8


def test_a_failed_read_is_not_cached_as_the_answer(monkeypatch):
    """A transient RPC failure must not pin the fallback for the lifetime of
    the process — the next call asks again."""
    calls = _stub_rpc(monkeypatch, decimals_hex=None)
    svc = W.WalletService()
    svc._token_decimals
    svc._token_decimals
    assert calls.count("decimals") == 2


def test_an_implausible_contract_answer_is_rejected(monkeypatch):
    """Reading 0 or a huge value means we called something that is not the
    token we think it is. Prefer the configured number over nonsense."""
    monkeypatch.setenv("STABLECOIN_DECIMALS", "6")
    get_settings.cache_clear()
    _stub_rpc(monkeypatch, decimals_hex=hex(255))

    assert W.WalletService()._token_decimals == 6


def test_configured_token_decimals_needs_no_rpc(monkeypatch):
    """The RPC-free helper the x402 amount parser uses — settings first,
    network default second."""
    monkeypatch.setenv("STABLECOIN_DECIMALS", "2")
    get_settings.cache_clear()
    assert W.configured_token_decimals() == 2

    monkeypatch.delenv("STABLECOIN_DECIMALS")
    get_settings.cache_clear()
    assert W.configured_token_decimals() == 6  # Base Sepolia USDC default
