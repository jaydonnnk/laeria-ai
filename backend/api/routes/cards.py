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


class CardCheckoutRequest(BaseModel):
    # The issued card to pay with.
    card_opaque_id: str = Field(min_length=1)
    settlement_tx: str = Field(min_length=1)
    wallet_address: str = Field(min_length=42, max_length=42)
    # The product to buy (from a storefront pick).
    product_handle: str = Field(min_length=1)
    variant_id: str = Field(min_length=1)
    # The card's prepaid value — the checkout total may not exceed it.
    card_amount_sgd: float = Field(gt=0, le=30)
    # Optional: the pending_signature action this fulfils. When set, a successful
    # checkout marks that action executed and records the receipt on it.
    action_id: str | None = None


@router.post("/straitsx/checkout")
def straitsx_checkout(req: CardCheckoutRequest) -> dict:
    """Buy a real product with an issued StraitsX card: read the card's live
    credentials, verify the product's DOM price fits the card's balance, then
    drive the merchant checkout with the card and the user's Profile shipping.

    The card credentials are fetched, used, and dropped — never stored. This is
    the seam that turns 'the agent holds a funded card' into 'the agent bought
    the thing and it ships to you'."""
    import services.straitsx_card as sx
    from services.cards import CardDetails
    from services.checkout import (
        CheckoutCeilingViolation,
        execute_checkout,
        shipping_for_current_user,
    )
    from services.storefront import StorefrontService

    store = StorefrontService()
    try:
        verification = store.verify_product(req.product_handle)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"product verification failed: {exc}") from exc
    if not verification.get("available"):
        raise HTTPException(status_code=409, detail="product is not available")
    price = float(verification.get("price_usd") or 0)
    if price <= 0:
        raise HTTPException(status_code=502, detail="could not read a live product price")

    # The card is prepaid in SGD; the store prices in its own currency. Compared
    # 1:1 like the rest of the build (no FX invented) — the checkout's own total
    # gate re-checks against this ceiling before any card entry.
    ceiling = req.card_amount_sgd
    if price > ceiling:
        raise HTTPException(
            status_code=409,
            detail=f"product price {price:.2f} exceeds the card's {ceiling:.2f} balance",
        )

    try:
        creds = sx.card_credentials(req.card_opaque_id, req.settlement_tx, req.wallet_address)
    except sx.StraitsXCardError as exc:
        raise HTTPException(status_code=502, detail=f"could not read card: {exc}") from exc
    card = CardDetails(
        number=creds["number"], cvc=creds["cvc"],
        exp_month=creds["exp_month"], exp_year=creds["exp_year"],
        brand="Visa", name=creds["name"] or "Laeria Agent",
    )

    try:
        result = execute_checkout(
            variant_id=req.variant_id,
            card=card,
            shipping=shipping_for_current_user(),
            mandate_ceiling_usd=ceiling,
            session_cookies=store._session_cookies(),
        )
    except CheckoutCeilingViolation as exc:
        _fail_action(req.action_id, str(exc), cancelled=True)
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        _fail_action(req.action_id, str(exc))
        raise HTTPException(status_code=502, detail=f"checkout failed: {exc}") from exc

    receipt = {
        "rail": "straitsx_card",
        "order_reference": result.order_reference,
        "gateway_profile": result.gateway_profile,
        "pan_shim": result.pan_shim,
        "checkout_screenshots": result.screenshots,
        # The receipt that ties the card authorization to the balance it drew on
        # (milestone 4): the on-chain XSGD settlement tx + the card it funded.
        "settlement_tx": req.settlement_tx,
        "card_opaque_id": req.card_opaque_id,
        # So the UI links the right explorer (Fuji vs C-Chain) for the tx.
        "env": get_settings().straitsx_card_env,
    }
    if req.action_id:
        try:
            repo.update_action(req.action_id, {
                "status": "executed",
                "amount_usd": result.total_usd,
                "metadata": {**(repo.get_action(req.action_id) or {}).get("metadata", {}), **receipt},
            })
        except Exception:  # noqa: BLE001 - the order exists on-chain; a DB miss must not 500
            pass

    return {
        "order_reference": result.order_reference,
        "total_usd": result.total_usd,
        "gateway_profile": result.gateway_profile,
        "pan_shim": result.pan_shim,
        "screenshots": result.screenshots,
        "product_handle": req.product_handle,
        "card_opaque_id": req.card_opaque_id,
    }


def _fail_action(action_id: str | None, error: str, *, cancelled: bool = False) -> None:
    """Record a checkout failure on the pending action, if there is one. A card
    that was funded but couldn't check out is an honest 'funded, not ordered'
    on the receipt — never a silent drop."""
    if not action_id:
        return
    try:
        meta = (repo.get_action(action_id) or {}).get("metadata", {})
        repo.update_action(action_id, {
            "status": "cancelled" if cancelled else "failed",
            "metadata": {**meta, "error": error},
        })
    except Exception:  # noqa: BLE001
        pass


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
