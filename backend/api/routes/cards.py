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


# ---- StraitsX non-custodial card issuance (MCP + x402) ----
#
# The card is paid for by an EIP-3009 XSGD payment the USER signs in their own
# wallet (option A, non-custodial): the backend builds the challenge and, after
# the browser returns a signature, assembles and submits the x402 payload. The
# backend never holds the paying key. See services/straitsx_card.py.


class CardChallengeRequest(BaseModel):
    wallet_address: str = Field(min_length=42, max_length=42)
    cardholder_name: str = Field(min_length=2, max_length=26)
    amount_sgd: float = Field(ge=5, le=30)  # issuer floor is 5, ceiling 30


class CardIssueRequest(BaseModel):
    # The challenge returned by /challenge, round-tripped, plus the signature the
    # wallet produced over challenge.typed_data.
    challenge: dict
    signature: str = Field(min_length=4)


class CardViewRequest(BaseModel):
    card_opaque_id: str = Field(min_length=1)
    settlement_tx: str = Field(min_length=1)
    wallet_address: str = Field(min_length=42, max_length=42)


@router.post("/straitsx/challenge")
def straitsx_challenge(req: CardChallengeRequest) -> dict:
    """Prepare a card purchase: ask the issuer, trigger the 402, and return the
    EIP-712 TransferWithAuthorization for the user's wallet to sign. No money
    moves and nothing is signed here."""
    import services.straitsx_card as sx

    try:
        mcp = sx.mcp_get_card(req.wallet_address, req.cardholder_name, req.amount_sgd)
        challenge = sx.build_challenge(sx._cardapi_url(mcp), req.amount_sgd, req.cardholder_name)
        return sx.prepare_signing(challenge, req.wallet_address)
    except sx.StraitsXCardError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/straitsx/issue")
def straitsx_issue(req: CardIssueRequest) -> dict:
    """Submit the signed authorization to settle the x402 payment and mint the
    card. The signature came from the user's wallet — the backend only relays."""
    import services.straitsx_card as sx

    from_address = (req.challenge.get("authorization") or {}).get("from")
    if not from_address:
        raise HTTPException(status_code=400, detail="challenge missing signer address")
    try:
        result = sx.submit_payment(req.challenge, from_address, req.signature)
    except sx.StraitsXCardError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    # Persist a card row (last4 only — never the PAN). card_html carries the
    # secrets and is returned to the client for this one response, not stored.
    try:
        repo.create_card(
            issuer="straitsx",
            issuer_card_id=result.get("card_opaque_id", ""),
            last4="",
            exp_month=0,
            exp_year=0,
            spend_limit_usd=float(str(result.get("amount_sgd", "0")).replace(",", "")),
            status="active",
            action_id=None,
            metadata={
                "settlement_tx": result.get("settlement_tx", ""),
                "payer": from_address,
                "env": get_settings().straitsx_card_env,
            },
        )
    except Exception:  # noqa: BLE001 - the card exists on-chain; a DB miss must not 500
        pass
    return result


@router.post("/straitsx/view")
def straitsx_view(req: CardViewRequest) -> dict:
    """Fetch a fresh one-time card view (iframe URL + rendered card_html)."""
    import services.straitsx_card as sx

    try:
        return sx.mcp_view_card(req.card_opaque_id, req.settlement_tx, req.wallet_address)
    except sx.StraitsXCardError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


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
