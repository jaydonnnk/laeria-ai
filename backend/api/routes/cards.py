"""Card routes — issue, inspect, cancel disposable virtual cards.

Card credentials (PAN/CVC) are never persisted: /cards lists DB rows (last4
only); /cards/{id}/details live-fetches from the issuer on demand. The
test-issue endpoint exists so the card UI is demoable standalone before the
checkout executor (Phase 3) starts issuing cards inside the action pipeline.
Auth via the router-level require_owner dependency in api/main.py.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from core.config import get_settings
from db import repositories as repo
from services.cards import get_issuer

router = APIRouter(prefix="/cards", tags=["cards"])


class TestIssueRequest(BaseModel):
    amount_limit_usd: float = Field(gt=0, le=10_000)
    merchant_hint: str = ""


@router.get("/")
def list_cards() -> list[dict]:
    return repo.list_cards()


@router.post("/test-issue")
def test_issue(req: TestIssueRequest) -> dict:
    """Dev/demo: issue a card directly, outside the action pipeline.
    Disabled outside development so the pipeline stays the only issuance
    path in anything resembling production."""
    if get_settings().app_env != "development":
        raise HTTPException(status_code=403, detail="test-issue is dev-only")
    issuer = get_issuer()
    try:
        card = issuer.issue(req.amount_limit_usd, merchant_hint=req.merchant_hint)
    except NotImplementedError as exc:
        raise HTTPException(status_code=501, detail=str(exc))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"issuer error: {exc}")
    return repo.create_card(
        issuer=card.issuer,
        issuer_card_id=card.issuer_card_id,
        last4=card.last4,
        exp_month=card.exp_month,
        exp_year=card.exp_year,
        spend_limit_usd=card.spend_limit_usd,
        status=card.status,
        metadata={"merchant_hint": req.merchant_hint, "source": "test-issue"},
    )


@router.get("/{card_id}/details")
def details(card_id: str) -> dict:
    """Live-fetch full credentials from the issuer. Nothing is stored."""
    row = repo.get_card(card_id)
    if not row:
        raise HTTPException(status_code=404, detail="card not found")
    if row["status"] == "canceled":
        raise HTTPException(status_code=409, detail="card is canceled")
    try:
        d = get_issuer().get_details(row["issuer_card_id"])
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"issuer error: {exc}")
    return {
        "number": d.number,
        "cvc": d.cvc,
        "exp_month": d.exp_month,
        "exp_year": d.exp_year,
        "brand": d.brand,
        "name": d.name,
    }


@router.post("/{card_id}/cancel")
def cancel(card_id: str) -> dict:
    row = repo.get_card(card_id)
    if not row:
        raise HTTPException(status_code=404, detail="card not found")
    if row["status"] == "canceled":
        return row
    try:
        get_issuer().cancel(row["issuer_card_id"])
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"issuer error: {exc}")
    updated = repo.update_card_status(card_id, "canceled")
    return updated or row


@router.get("/{card_id}/transactions")
def transactions(card_id: str) -> list[dict]:
    """Issuer-side transactions — the receipt view's proof that the issued
    card actually authorized the amount."""
    row = repo.get_card(card_id)
    if not row:
        raise HTTPException(status_code=404, detail="card not found")
    try:
        return get_issuer().list_transactions(row["issuer_card_id"])
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"issuer error: {exc}")
