"""Signed mandate delegation — the 'Delegate' step of the payment lifecycle.

The mandate is a set of spend rules stored server-side; the delegation is the
user's own wallet signing those exact rules over EIP-712. Verifying it recovers
the signer and confirms it is the connected wallet, so the agent's spending
authority traces to a signature the user made *beforehand* — not a row we could
have written on their behalf. "Trust moves from a challenge afterwards to a
signature beforehand."

The EIP-712 schema is owned here, not taken from the client: the frontend must
build the same typed data to get MetaMask to sign, but the backend rebuilds it
from its own definition before recovering, so a client cannot silently change
the field set the signature is checked against.
"""
from __future__ import annotations

from time import time

from core.logging import get_logger

logger = get_logger(__name__)

DOMAIN_NAME = "laeria.ai"
DOMAIN_VERSION = "1"

# Field order is part of the EIP-712 hash — it MUST match the frontend's typed
# data exactly, or recovery yields a different (wrong) address.
MANDATE_TYPE = [
    {"name": "maxPerTransaction", "type": "uint256"},
    {"name": "maxPerMonth", "type": "uint256"},
    {"name": "requireConfirmationAbove", "type": "uint256"},
    {"name": "autonomous", "type": "bool"},
    {"name": "expiry", "type": "uint256"},
    {"name": "nonce", "type": "uint256"},
]

_CAP_FIELDS = {
    "maxPerTransaction": "max_per_transaction",
    "maxPerMonth": "max_per_month",
    "requireConfirmationAbove": "require_confirmation_above",
}


class DelegationError(ValueError):
    pass


def cap_units(value) -> int:
    """A mandate cap (float token amount, or None) as integer base units.
    None -> 0, matching 'an unset cap is zero allowance'."""
    if value in (None, ""):
        return 0
    from services.wallet import configured_token_decimals

    return int(round(float(value) * (10 ** configured_token_decimals())))


def _signable(message: dict, chain_id: int):
    from eth_account.messages import encode_typed_data

    domain = {"name": DOMAIN_NAME, "version": DOMAIN_VERSION, "chainId": int(chain_id)}
    types = {"SpendingMandate": MANDATE_TYPE}
    msg = {
        "maxPerTransaction": int(message["maxPerTransaction"]),
        "maxPerMonth": int(message["maxPerMonth"]),
        "requireConfirmationAbove": int(message["requireConfirmationAbove"]),
        "autonomous": bool(message["autonomous"]),
        "expiry": int(message["expiry"]),
        "nonce": int(message["nonce"]),
    }
    return encode_typed_data(domain, types, msg)


def recover_signer(message: dict, chain_id: int, signature: str) -> str:
    from eth_account import Account

    return Account.recover_message(_signable(message, chain_id), signature=signature)


def verify_delegation(
    *, message: dict, chain_id: int, signature: str, expected_signer: str, mandate: dict
) -> dict:
    """Recover the signer and confirm the signed rules ARE this user's mandate,
    from THEIR connected wallet, and unexpired. Returns the delegation record to
    store; raises DelegationError on any mismatch."""
    if not expected_signer:
        raise DelegationError("connect a wallet before signing a delegation")

    required = {"maxPerTransaction", "maxPerMonth", "requireConfirmationAbove",
                "autonomous", "expiry", "nonce"}
    if not required.issubset(message):
        raise DelegationError("delegation message is missing required fields")

    try:
        signer = recover_signer(message, chain_id, signature)
    except DelegationError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise DelegationError(f"could not recover a signer from the signature: {exc}") from exc

    if signer.lower() != expected_signer.lower():
        raise DelegationError(
            "the signature was not made by the connected wallet "
            f"({signer} != {expected_signer})"
        )

    # The signed numbers must be THIS mandate's caps, or the credential would
    # authorise something other than what is stored.
    for field, cap_key in _CAP_FIELDS.items():
        if int(message[field]) != cap_units(mandate.get(cap_key)):
            raise DelegationError(
                "the signed delegation does not match the current mandate — "
                "re-sign after changing the caps"
            )

    if int(message["expiry"]) <= int(time()):
        raise DelegationError("this delegation has already expired")

    return {
        "signature": signature,
        "signed_by": signer,
        "chain_id": int(chain_id),
        "message": message,
        "signed_at": int(time()),
    }
