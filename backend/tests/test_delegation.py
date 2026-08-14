"""Signed mandate delegation: recover the signer and bind it to the mandate.

Signs real EIP-712 typed data with eth_account and verifies the backend
recovers the same address — the exact contract the frontend's MetaMask signature
must satisfy. The rejections matter as much as the happy path: a signature from
the wrong wallet, over stale caps, or past its expiry must not verify.
"""
from __future__ import annotations

import time

import pytest
from eth_account import Account

from core.config import get_settings
from services import delegation as D

KEY = "0x" + "11" * 32
CHAIN = 43113


@pytest.fixture(autouse=True)
def _decimals(monkeypatch):
    # Fuji default token is 6 decimals; make it explicit so cap_units is stable.
    monkeypatch.setenv("X402_NETWORK", "eip155:43113")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _mandate() -> dict:
    return {
        "max_per_transaction": 30.0,
        "max_per_month": 100.0,
        "require_confirmation_above": 10.0,
    }


def _message(mandate: dict, *, autonomous=True, expiry=None, nonce=1, **overrides) -> dict:
    msg = {
        "maxPerTransaction": str(D.cap_units(mandate.get("max_per_transaction"))),
        "maxPerMonth": str(D.cap_units(mandate.get("max_per_month"))),
        "requireConfirmationAbove": str(D.cap_units(mandate.get("require_confirmation_above"))),
        "autonomous": autonomous,
        "expiry": expiry if expiry is not None else int(time.time()) + 3600,
        "nonce": nonce,
    }
    msg.update(overrides)
    return msg


def _sign(message: dict, key: str = KEY, chain: int = CHAIN) -> tuple[str, str]:
    acct = Account.from_key(key)
    signed = Account.sign_message(D._signable(message, chain), key)
    return acct.address, signed.signature.to_0x_hex()


def test_a_valid_signature_recovers_the_signer_and_binds_the_caps():
    m = _mandate()
    msg = _message(m)
    signer, sig = _sign(msg)
    record = D.verify_delegation(
        message=msg, chain_id=CHAIN, signature=sig, expected_signer=signer, mandate=m
    )
    assert record["signed_by"].lower() == signer.lower()
    assert record["message"] == msg


def test_a_signature_from_another_wallet_is_rejected():
    m = _mandate()
    msg = _message(m)
    _, sig = _sign(msg)
    other = Account.from_key("0x" + "22" * 32).address
    with pytest.raises(D.DelegationError, match="connected wallet"):
        D.verify_delegation(
            message=msg, chain_id=CHAIN, signature=sig, expected_signer=other, mandate=m
        )


def test_caps_that_dont_match_the_mandate_are_rejected():
    """The signature is genuine, but over a HIGHER per-transaction cap than the
    stored mandate — the credential must not authorise more than the mandate."""
    m = _mandate()
    tampered = _message(m, maxPerTransaction=str(D.cap_units(999.0)))
    signer, sig = _sign(tampered)
    with pytest.raises(D.DelegationError, match="does not match"):
        D.verify_delegation(
            message=tampered, chain_id=CHAIN, signature=sig, expected_signer=signer, mandate=m
        )


def test_an_expired_delegation_is_rejected():
    m = _mandate()
    msg = _message(m, expiry=int(time.time()) - 10)
    signer, sig = _sign(msg)
    with pytest.raises(D.DelegationError, match="expired"):
        D.verify_delegation(
            message=msg, chain_id=CHAIN, signature=sig, expected_signer=signer, mandate=m
        )


def test_a_garbage_signature_is_rejected_not_crashed():
    m = _mandate()
    msg = _message(m)
    with pytest.raises(D.DelegationError):
        D.verify_delegation(
            message=msg, chain_id=CHAIN, signature="0xdeadbeef",
            expected_signer=Account.from_key(KEY).address, mandate=m,
        )


def test_null_caps_encode_as_zero_units():
    assert D.cap_units(None) == 0
    assert D.cap_units("") == 0
    assert D.cap_units(30.0) == 30_000_000  # 6 decimals
