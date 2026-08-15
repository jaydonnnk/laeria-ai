"""Self-facilitated x402 (WS-x402): the vendor declares XSGD and wires a local
facilitator instead of an external one.

No network here — construction and requirement-building are offline. The on-chain
settle is exercised by hand on Fuji (see the WS-x402 test steps).
"""
from __future__ import annotations

import pytest

from core.config import get_settings
from services import payment as P
from services import x402_facilitator as F

XSGD = "0x" + "ab" * 20


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("X402_NETWORK", "eip155:43113")
    monkeypatch.setenv("X402_SELF_FACILITATE", "true")
    monkeypatch.setenv("X402_TREASURY_ADDRESS", "0x" + "cc" * 20)
    monkeypatch.setenv("X402_TREASURY_PRIVATE_KEY", "0x" + "11" * 32)
    monkeypatch.setenv("STABLECOIN_CONTRACT", XSGD)
    monkeypatch.setenv("STABLECOIN_SYMBOL", "XSGD")
    monkeypatch.setenv("AVALANCHE_FUJI_RPC_URL", "https://api.avax-test.network/ext/bc/C/rpc")
    get_settings.cache_clear()
    P._resource_server.cache_clear()
    F.local_facilitator.cache_clear()
    yield
    get_settings.cache_clear()
    P._resource_server.cache_clear()
    F.local_facilitator.cache_clear()


def test_price_for_declares_xsgd_when_self_facilitating():
    from x402.schemas.base import AssetAmount

    price = P._price_for(0.01)
    assert isinstance(price, AssetAmount)
    assert price.asset == XSGD
    assert price.amount == "10000"  # 0.01 XSGD at 6 decimals
    assert price.extra == {"name": "XSGD", "version": "2"}


def test_price_for_falls_back_to_dollars_without_self_facilitation(monkeypatch):
    monkeypatch.setenv("X402_SELF_FACILITATE", "false")
    get_settings.cache_clear()
    assert P._price_for(0.01) == "$0.01"


def test_build_requirements_carry_the_xsgd_asset_and_eip712_domain():
    reqs = P.build_requirements(0.01, "/vendor/deep-report")
    reqs = reqs if isinstance(reqs, list) else [reqs]
    r = reqs[0].model_dump(by_alias=True)
    assert r["scheme"] == "exact"
    assert str(r["asset"]).lower() == XSGD
    assert r["amount"] == "10000"
    assert r["extra"]["name"] == "XSGD"
    assert r["extra"]["version"] == "2"


def test_local_facilitator_builds_with_a_relayer():
    fac = F.local_facilitator()
    assert fac is not None
