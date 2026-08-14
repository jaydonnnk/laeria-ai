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

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from core.logging import get_logger
from services.wallet import WalletError, WalletService, ensure_user_wallet

logger = get_logger(__name__)

router = APIRouter(prefix="/wallet", tags=["wallet"])


class FundRequest(BaseModel):
    amount_usd: float = Field(gt=0, le=1000)


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
