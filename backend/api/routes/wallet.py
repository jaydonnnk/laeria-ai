"""Wallet routes — funding pillar. Balances are read-only; funding moves the
configured TESTNET stablecoin treasury → the caller's own agent wallet (any
mainnet is refused in the service). Auth via the router-level require_user
dependency in api/main.py: every signed-in account has its own wallet now.

Provisioning is lazy. The first authenticated read of a new account's balances
generates its wallet and seeds it from the treasury (gas + XSGD), so a user
never has to ask for a wallet — visiting Commerce is enough. It is idempotent
and, for the owner or when WALLET_ENCRYPTION_KEY is unset, a no-op that leaves
the single env wallet in place.
"""

from __future__ import annotations

import re

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, field_validator

from core.logging import get_logger
from services.wallet import WalletError, WalletService, ensure_user_wallet

logger = get_logger(__name__)

router = APIRouter(prefix="/wallet", tags=["wallet"])

_ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")


class FundRequest(BaseModel):
    amount_usd: float = Field(gt=0, le=1000)


class ConnectRequest(BaseModel):
    address: str

    @field_validator("address")
    @classmethod
    def _valid_address(cls, v: str) -> str:
        v = v.strip()
        if not _ADDRESS_RE.match(v):
            raise ValueError("not a valid EVM address")
        return v


def _provision() -> dict:
    """Ensure the caller has a wallet before a balance read or a fund. Never
    fatal: a provisioning hiccup (e.g. treasury funding failed) must not stop
    the balances/fund call from running against whatever wallet does exist."""
    try:
        return ensure_user_wallet()
    except Exception as exc:  # noqa: BLE001
        logger.error("wallet provisioning failed: %s", exc)
        return {"provisioned": False, "reason": str(exc)}


@router.get("/balances")
def balances() -> dict:
    provision = _provision()
    try:
        out = WalletService().balances()
    except WalletError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    # Surface how the wallet was obtained so the UI can say "new wallet funded"
    # on a first visit without a second round trip.
    out["provision"] = provision
    return out


@router.post("/fund")
def fund(req: FundRequest) -> dict:
    _provision()
    try:
        return WalletService().fund_agent(req.amount_usd)
    except WalletError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"funding failed: {exc}")


@router.post("/connect")
def connect(req: ConnectRequest) -> dict:
    """Register the caller's self-custodied wallet (non-custodial path). Stores
    the address only — never a key — so the operator can spend it later within
    an allowance the user signs. Overrides any custodial wallet for this user:
    connecting is an explicit choice to hold your own keys."""
    from db import repositories as repo

    repo.set_user_wallet(req.address, "")
    logger.info("connected non-custodial wallet %s", req.address)
    return {"address": req.address, "custodial": False}


@router.get("/operator")
def operator() -> dict:
    """Who the user approves, and on what token/chain — everything the frontend
    needs to build the ERC-20 approve(operator, cap) transaction."""
    try:
        svc = WalletService()
    except WalletError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    op = svc.operator_address()
    if not op:
        raise HTTPException(status_code=503, detail="no operator key configured")
    return {
        "operator_address": op,
        "token_contract": svc._net["token"],
        "token_symbol": svc._net["token_symbol"],
        "token_decimals": svc._token_decimals,
        "chain_id": svc.expected_chain_id(),
        "network": svc._net["name"],
        "network_id": svc._settings.x402_network,
    }


@router.get("/allowance")
def allowance() -> dict:
    """The connected wallet's current backing: balance + how much the agent is
    still approved to spend. Drives the UI's 'approve more' prompt."""
    try:
        svc = WalletService()
        wallet = svc.resolve_agent_wallet()
    except WalletError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    addr = wallet.get("address") or ""
    out: dict = {
        "address": addr,
        "custodial": wallet.get("custodial", False),
        "operator_address": svc.operator_address(),
        "token_symbol": svc._net["token_symbol"],
    }
    if not addr:
        out.update({"balance": 0.0, "allowance": 0.0})
        return out
    try:
        out["balance"] = svc.token_balance(addr)
        # A custodial wallet signs its own transfers, so "allowance" is moot —
        # report its full balance as spendable rather than a misleading zero.
        out["allowance"] = (
            out["balance"] if wallet.get("custodial") else svc.allowance(addr)
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"cannot read chain state: {exc}")
    return out
