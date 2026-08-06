"""Wallet routes — funding pillar. Balances are read-only; funding moves the
configured TESTNET stablecoin treasury → agent (any mainnet is refused in the
service). Auth via the router-level require_owner dependency in api/main.py."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from services.wallet import WalletError, WalletService

router = APIRouter(prefix="/wallet", tags=["wallet"])


class FundRequest(BaseModel):
    amount_usd: float = Field(gt=0, le=1000)


@router.get("/balances")
def balances() -> dict:
    try:
        return WalletService().balances()
    except WalletError as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@router.post("/fund")
def fund(req: FundRequest) -> dict:
    try:
        return WalletService().fund_agent(req.amount_usd)
    except WalletError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"funding failed: {exc}")
