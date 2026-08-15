"""Action routes — Phase 4: agentic payment under a standing mandate.

Lifecycle: propose -> (within mandate & below confirm threshold & autonomy
enabled) execute immediately, else pending_approval with a 10-minute window
-> approve executes, reject/timeout cancels. Above-threshold actions are
NEVER executed silently.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from core.logging import get_logger
from core.models import ActionMandate

logger = get_logger(__name__)

router = APIRouter(prefix="/actions", tags=["actions"])

APPROVAL_WINDOW_MINUTES = 10


class MandateUpdate(BaseModel):
    # None = unset = zero allowance (see ActionMandate). There is no value
    # that means "unlimited"; omitting a cap denies rather than permits.
    max_per_transaction: float | None = Field(default=None, ge=0)
    max_per_month: float | None = Field(default=None, ge=0)
    require_confirmation_above: float | None = Field(default=None, ge=0)
    allowed_categories: list[str] = []
    blocked_vendors: list[str] = []
    autonomous_actions_enabled: bool = False


class ProposeRequest(BaseModel):
    type: str = Field(pattern="^(purchase|cancel_subscription|order_replacement)$")
    target_url: str = Field(min_length=8, max_length=500)
    category: str = ""
    description: str = ""
    # Payment rail: "x402" (crypto, the original path) or "card" (disposable
    # virtual card + browser checkout on the demo storefront).
    rail: str = Field(default="x402", pattern="^(x402|card)$")
    # Card rail only: which storefront product is being bought.
    product_handle: str = ""
    variant_id: str = ""


def _mandate() -> ActionMandate:
    from db import repositories as repo

    return ActionMandate(**repo.get_mandate())


def _stablecoin_backing(required: float) -> float:
    """The agent's on-chain stablecoin balance, as a spending ceiling.

    Raises MandateViolation when the balance cannot cover the purchase, or
    cannot be read at all. Both are refusals rather than failures: "the agent
    cannot spend what it does not hold" is a spending rule, and it belongs in
    the same category as the mandate caps, with the same fail-closed direction
    the rest of this module uses — an unknown balance is zero allowance, never
    unlimited.

    UNITS: the balance is in token units and `required` is in the storefront's
    currency, and this compares them 1:1. That is exact when the store prices
    in the token's peg (an SGD store against XSGD) and approximate otherwise.
    The refusal message names the token symbol so a mismatch is visible on
    screen rather than buried in a constant — no FX rate is invented here.
    """
    from services.payment import MandateViolation
    from services.wallet import WalletService

    try:
        svc = WalletService()
        wallet = svc.resolve_agent_wallet()
        symbol = svc._net.get("token_symbol") or "tokens"
    except Exception as exc:  # noqa: BLE001
        raise MandateViolation(
            f"cannot resolve the agent wallet ({exc}) — refusing to issue a card "
            "when nothing is known to back it"
        ) from exc

    addr = wallet.get("address") or ""
    if not addr:
        raise MandateViolation(
            "no wallet backs this purchase — connect a wallet (or fund the "
            "custodial one) before the agent can buy"
        )

    try:
        balance = svc.token_balance(addr)
    except Exception as exc:  # noqa: BLE001
        raise MandateViolation(
            f"cannot read the wallet's stablecoin balance ({exc}) — refusing to "
            "issue a card when nothing is known to back it"
        ) from exc

    # Custodial: the wallet's own balance is the ceiling. Non-custodial: the
    # agent can spend only what the user APPROVED, so the ceiling is the lesser
    # of the balance and the remaining allowance — a card can never outrun the
    # allowance any more than it can outrun the funds.
    if wallet.get("custodial"):
        ceiling = balance
    else:
        try:
            allowed = svc.allowance(addr)
        except Exception as exc:  # noqa: BLE001
            raise MandateViolation(
                f"cannot read the agent's spending allowance ({exc}) — refusing "
                "to issue a card against an unknown allowance"
            ) from exc
        if allowed + 1e-9 < required:
            raise MandateViolation(
                f"the wallet approved only {allowed:.2f} {symbol} of agent "
                f"spending but this purchase needs {required:.2f} — approve more "
                "for the agent before it can buy"
            )
        ceiling = min(balance, allowed)

    if ceiling + 1e-9 < required:
        raise MandateViolation(
            f"wallet backs {ceiling:.2f} {symbol} but this purchase needs "
            f"{required:.2f} — fund the wallet before it can buy"
        )
    return ceiling


def _settle_on_chain(amount: float) -> dict:
    """Move `amount` of the stablecoin agent → merchant for a completed order.

    Never raises. The caller invokes this only once the order already exists,
    at which point the purchase is a fact and the only open question is
    whether the chain leg succeeded — so a failure is a field in the receipt,
    not an exception that would relabel a bought item as a crash. The
    distinction is visible on the receipt: a settlement with `error` set is an
    honest "ordered, not yet settled", which is a true statement about the
    world and a recoverable one.
    """
    from services.wallet import WalletService

    try:
        out = WalletService().settle_purchase(amount)
        logger.info("settled %.2f on-chain: %s", amount, out["tx_hash"])
        return {"settled": True, **out}
    except Exception as exc:  # noqa: BLE001
        logger.error("on-chain settlement failed (order already placed): %s", exc)
        return {"settled": False, "error": str(exc), "amount_usd": round(amount, 2)}


def _execute(action_id: str, target_url: str, approved_usd: float = 0.0) -> dict:
    """Pay the target and mark the action executed. Failure marks it failed.

    Defense in depth: rediscovers the price at execution time, refuses if it
    drifted above what was approved, re-verifies the hard mandate caps against
    the ACTUAL amount, then hands pay_402 the approved figure as a ceiling. A
    price that appeared only after the mandate check (vendor error during
    discovery, or a changed price) can therefore neither exceed the mandate
    nor quietly exceed what a human consented to."""
    from core.config import get_settings
    from db import repositories as repo
    from services.payment import MandateViolation, PaymentService, vendor_host

    try:
        svc = PaymentService()
        offer = svc.check_402(target_url)
        actual = float(offer["amount_usd"]) if offer else 0.0

        # Consent binds to an amount. Without this the mandate recheck below
        # compares the new price against itself and passes vacuously.
        tolerance = get_settings().price_drift_tolerance
        ceiling = actual
        if approved_usd > 0:
            ceiling = min(actual, approved_usd * (1 + tolerance))
            if actual > approved_usd * (1 + tolerance):
                repo.update_action(
                    action_id,
                    {
                        "status": "pending_approval",
                        "amount_usd": actual,
                        "metadata": {
                            **(repo.get_action(action_id) or {}).get("metadata", {}),
                            "expires_at": (
                                datetime.now(timezone.utc)
                                + timedelta(minutes=APPROVAL_WINDOW_MINUTES)
                            ).isoformat(),
                            "reprice": {"approved": approved_usd, "now": actual},
                        },
                    },
                )
                raise HTTPException(
                    status_code=409,
                    detail=(
                        f"price moved ${approved_usd:.2f} → ${actual:.2f} since "
                        f"approval (tolerance {tolerance:.0%}) — re-approval required"
                    ),
                )

        # Hard amount caps only — category/vendor were re-checked against the
        # live mandate here because PUT /actions/mandate can change them
        # inside the approval window.
        recheck_mandate = _mandate()
        svc.verify_within_mandate(
            amount_usd=actual,
            category="",
            mandate=recheck_mandate.model_copy(update={"allowed_categories": []}),
            spent_this_month=repo.executed_spend_this_month(),
            vendor=vendor_host(target_url),
        )
        result = svc.pay_402(target_url, max_amount_usd=ceiling)
    except MandateViolation as exc:
        repo.update_action(action_id, {"status": "cancelled",
                                       "metadata": {"mandate_violation": str(exc)}})
        raise HTTPException(status_code=409, detail=f"mandate violation at execution: {exc}") from exc
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        repo.update_action(action_id, {"status": "failed", "metadata": {"error": str(exc)}})
        raise HTTPException(status_code=502, detail=f"execution failed: {exc}") from exc
    return repo.update_action(
        action_id,
        {
            "status": "executed",
            "amount_usd": result["amount_usd"],
            "metadata": {
                "receipt": result["receipt"],
                "content_preview": (result["content"] or "")[:400],
            },
        },
    )


def _execute_card_purchase(action: dict) -> dict:
    """Card-rail execution: browser-verify the live price, re-check the hard
    caps, issue a disposable card capped just above that price, drive the
    checkout (which re-reads the total from the checkout page before card
    entry — third verification layer), then cancel the card no matter what.

    The card dies in the finally block: success or failure, a disposable
    card never outlives its one purchase attempt."""
    from core.config import get_settings
    from db import repositories as repo
    from services.cards import StripeIssuingAdapter, get_issuer
    from services.checkout import (
        CheckoutCeilingViolation,
        execute_checkout,
        shipping_for_current_user,
    )
    from services.payment import MandateViolation, PaymentService, vendor_host
    from services.storefront import StorefrontService

    action_id = action["id"]
    meta = dict(action.get("metadata") or {})
    handle = meta.get("product_handle", "")
    variant_id = meta.get("variant_id", "")

    try:
        # Live DOM price — what a human buyer sees right now.
        store = StorefrontService()
        verification = store.verify_product(handle)
        dom_price = float(verification["price_usd"])
        if dom_price <= 0:
            raise RuntimeError("could not read a live price from the product page")
        if not verification["available"]:
            raise RuntimeError("product is no longer available")
        # Products with subscription selling plans expose the plan price in
        # og:price while the cart charges the one-time price — verify against
        # the HIGHER of DOM and listing so the ceiling covers what checkout
        # will actually charge (the in-checkout total gate still has the
        # final word).
        approved = float(action.get("amount_usd") or 0)
        actual = max(dom_price, approved)

        # Consent was given for `approved`. Nothing below may quietly spend
        # more than that: without this the "three verification layers" all
        # compare the current price against the current price, and a price
        # that moved inside the approval window executes at the new number.
        tolerance = get_settings().price_drift_tolerance
        if approved > 0 and actual > approved * (1 + tolerance):
            repo.update_action(
                action_id,
                {
                    "status": "pending_approval",
                    "expires_at": (
                        datetime.now(timezone.utc)
                        + timedelta(minutes=APPROVAL_WINDOW_MINUTES)
                    ).isoformat(),
                    "amount_usd": actual,
                    "metadata": {
                        **meta,
                        "reprice": {"approved": approved, "now": actual},
                    },
                },
            )
            raise HTTPException(
                status_code=409,
                detail=(
                    f"price moved ${approved:.2f} → ${actual:.2f} since approval "
                    f"(tolerance {tolerance:.0%}) — re-approval required"
                ),
            )

        # Re-read the mandate live: PUT /actions/mandate can change caps and
        # blocked_vendors inside the approval window, so nothing checked at
        # propose time can be assumed still true here. allowed_categories is
        # the one exception — the product's category is not re-derivable from
        # the storefront, so it stays checked at propose only.
        recheck_mandate = _mandate().model_copy(update={"allowed_categories": []})
        PaymentService().verify_within_mandate(
            amount_usd=actual,
            category="",
            mandate=recheck_mandate,
            spent_this_month=repo.executed_spend_this_month(),
            vendor=vendor_host(action.get("target") or ""),
        )
        # What actually backs this card. Until this existed the funding rail
        # and the card rail were strangers: the agent's wallet could hold
        # nothing at all and a card would still be issued against the mandate
        # alone. A card the stablecoin cannot cover is an unbacked credit line,
        # which is the one thing this architecture is supposed not to be.
        backing = _stablecoin_backing(actual)
    except MandateViolation as exc:
        repo.update_action(action_id, {"status": "cancelled",
                                       "metadata": {**meta, "mandate_violation": str(exc)}})
        raise HTTPException(status_code=409, detail=f"mandate violation at execution: {exc}") from exc
    except HTTPException:
        # Already a considered response (e.g. the reprice re-approval above) —
        # the action row is set; do not relabel it as a generic failure.
        raise
    except Exception as exc:  # noqa: BLE001
        repo.update_action(action_id, {"status": "failed",
                                       "metadata": {**meta, "error": str(exc)}})
        raise HTTPException(status_code=502, detail=f"execution failed: {exc}") from exc

    # Ceiling for the card limit AND the in-checkout total gate: verified
    # price + shipping/taxes allowance, hard-clamped to whatever mandate
    # headroom remains AND to what the human actually approved. The final
    # checkout total must fit under this or the executor refuses before card
    # entry.
    mandate_now = _mandate()
    headrooms = [
        float(actual) * 1.05 + get_settings().checkout_shipping_buffer_usd
    ]
    if approved > 0:
        headrooms.append(approved * (1 + tolerance) + get_settings().checkout_shipping_buffer_usd)
    if mandate_now.max_per_transaction is not None:
        headrooms.append(mandate_now.max_per_transaction)
    if mandate_now.max_per_month is not None:
        headrooms.append(
            mandate_now.max_per_month - repo.executed_spend_this_month()
        )
    # The stablecoin backing is a spending limit like every other one here, so
    # it belongs in the same min() rather than in a check of its own. This is
    # what makes "the card is backed by the wallet" a property of the code
    # instead of a claim in a pitch.
    headrooms.append(backing)
    ceiling = round(max(min(headrooms), 0.0), 2)
    issuer = get_issuer()
    issued = None
    card_row = None
    try:
        issued = issuer.issue(ceiling, merchant_hint=meta.get("description", "")[:80])
        card_row = repo.create_card(
            issuer=issued.issuer,
            issuer_card_id=issued.issuer_card_id,
            last4=issued.last4,
            exp_month=issued.exp_month,
            exp_year=issued.exp_year,
            spend_limit_usd=issued.spend_limit_usd,
            status="active",
            action_id=action_id,
            metadata={"product_handle": handle},
        )
        details = issuer.get_details(issued.issuer_card_id)
        result = execute_checkout(
            variant_id=variant_id,
            card=details,
            shipping=shipping_for_current_user(),
            mandate_ceiling_usd=ceiling,
            session_cookies=store._session_cookies(),
        )
        # Bogus profile: the storefront gateway saw the magic PAN, so put a
        # matching REAL authorization on the issued card where the issuer
        # supports simulation — the card of record shows the transaction.
        auth_id = None
        if result.pan_shim and isinstance(issuer, StripeIssuingAdapter):
            try:
                auth_id = issuer.simulate_authorization(
                    issued.issuer_card_id, result.total_usd
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("issuer auth simulation failed: %s", exc)
    except CheckoutCeilingViolation as exc:
        repo.update_action(action_id, {"status": "cancelled",
                                       "metadata": {**meta, "mandate_violation": str(exc)}})
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        repo.update_action(action_id, {"status": "failed",
                                       "metadata": {**meta, "error": str(exc)}})
        raise HTTPException(status_code=502, detail=f"execution failed: {exc}") from exc
    finally:
        # Disposable means disposable — the card dies here on every path.
        if issued is not None:
            try:
                issuer.cancel(issued.issuer_card_id)
                if card_row is not None:
                    repo.update_card_status(card_row["id"], "canceled")
            except Exception as exc:  # noqa: BLE001
                logger.error("card cancel after checkout failed: %s", exc)

    # On-chain settlement of what was just bought. Runs AFTER the order exists,
    # so its failure is RECORDED, never raised: the goods are paid for and the
    # action genuinely is executed. Letting a gas hiccup here mark a completed
    # purchase "failed" would be a worse lie than an unsettled receipt.
    settlement = _settle_on_chain(result.total_usd)

    return repo.update_action(
        action_id,
        {
            "status": "executed",
            "amount_usd": result.total_usd,
            "metadata": {
                **meta,
                "rail": "card",
                "settlement": settlement,
                "order_reference": result.order_reference,
                "gateway_profile": result.gateway_profile,
                "pan_shim": result.pan_shim,
                "card_id": card_row["id"] if card_row else None,
                "card_last4": issued.last4 if issued else None,
                "issuer_authorization": auth_id,
                "checkout_screenshots": result.screenshots,
                "verified_price_usd": actual,
                "product_screenshot": verification["screenshot_path"],
            },
        },
    )


def _execute_cancellation(action: dict) -> dict:
    """'Execute' a subscription cancellation. No consumer service exposes a
    cancel API, so the honest execution is: log the decision, write a
    reminder into the Obsidian vault (best-effort), and record that the final
    click is manual. Never pretends an API call happened."""
    from db import repositories as repo

    item = action["target"]
    note = f"CANCEL {item} — approved by owner; complete the cancellation in the service's account settings."
    vault_written = False
    try:
        from services.obsidian import ObsidianService

        ObsidianService().write_action_log(note)
        vault_written = True
    except Exception as exc:  # noqa: BLE001
        logger.info("obsidian action log skipped: %s", exc)

    return repo.update_action(
        action["id"],
        {
            "status": "executed",
            "metadata": {
                **(action.get("metadata") or {}),
                "execution": "manual-cancel",
                "note": note,
                "obsidian_reminder_written": vault_written,
            },
        },
    )


@router.get("/mandate")
def get_mandate() -> dict:
    from db import repositories as repo

    try:
        return repo.get_mandate()
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.put("/mandate")
def put_mandate(req: MandateUpdate) -> dict:
    from db import repositories as repo

    return repo.set_mandate(req.model_dump())


class DelegationRequest(BaseModel):
    # The EIP-712 message the wallet signed (the six SpendingMandate fields),
    # the chain it was signed for, and the signature.
    message: dict
    chain_id: int = Field(ge=1)
    signature: str = Field(min_length=4)


@router.post("/mandate/delegation")
def sign_delegation(req: DelegationRequest) -> dict:
    """Record the user's signed delegation over their mandate. Verified against
    the connected wallet and the current caps before it is stored — an
    unverifiable signature is rejected, never persisted."""
    from db import repositories as repo
    from services.delegation import DelegationError, verify_delegation

    wallet = repo.get_user_wallet()
    if not wallet or not wallet.get("address"):
        raise HTTPException(
            status_code=409, detail="connect a wallet before signing a delegation"
        )
    mandate = repo.get_mandate() or {}
    try:
        record = verify_delegation(
            message=req.message,
            chain_id=req.chain_id,
            signature=req.signature,
            expected_signer=wallet["address"],
            mandate=mandate,
        )
    except DelegationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    repo.set_delegation(record)
    return {
        "verified": True,
        "signed_by": record["signed_by"],
        "expiry": record["message"]["expiry"],
    }


@router.get("/mandate/delegation")
def read_delegation() -> dict:
    """The stored delegation, re-verified live against the current wallet and
    mandate so a since-changed cap shows as no-longer-valid rather than a stale
    green tick."""
    from time import time

    from db import repositories as repo
    from services.delegation import DelegationError, verify_delegation

    record = repo.get_delegation()
    if not record:
        return {"present": False}

    wallet = repo.get_user_wallet() or {}
    mandate = repo.get_mandate() or {}
    expiry = (record.get("message") or {}).get("expiry")
    out: dict = {
        "present": True,
        "signed_by": record.get("signed_by"),
        "expiry": expiry,
        "signed_at": record.get("signed_at"),
        "expired": bool(expiry and int(expiry) <= int(time())),
    }
    try:
        verify_delegation(
            message=record["message"],
            chain_id=record["chain_id"],
            signature=record["signature"],
            expected_signer=(wallet.get("address") or record.get("signed_by") or ""),
            mandate=mandate,
        )
        out["verified"] = True
    except DelegationError as exc:
        out["verified"] = False
        out["reason"] = str(exc)
    return out


@router.post("/propose")
def propose_action(req: ProposeRequest) -> dict:
    """Agent (or user) proposes a payable action. Executes autonomously only
    when the mandate clears it; otherwise parks it for approval."""
    from db import repositories as repo
    from services.payment import (
        MandateViolation,
        PaymentService,
        vendor_host,
    )

    mandate = _mandate()
    svc = PaymentService()

    # Price discovery: what does the target actually cost? A failed discovery
    # refuses outright — never treat an unreachable/erroring vendor as free.
    if req.rail == "card":
        if req.type != "purchase":
            raise HTTPException(status_code=422, detail="card rail supports purchases only")
        if not req.product_handle or not req.variant_id:
            raise HTTPException(
                status_code=422, detail="card rail needs product_handle and variant_id"
            )
        from services.storefront import StorefrontService

        try:
            product = StorefrontService().get_product(req.product_handle)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(
                status_code=502, detail=f"price discovery failed: {exc}"
            ) from exc
        if not product:
            raise HTTPException(status_code=404, detail="product not found in store")
        amount = float(product["price_usd"])
        if amount <= 0:
            raise HTTPException(status_code=502, detail="product has no readable price")
    else:
        try:
            offer = svc.check_402(req.target_url)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(
                status_code=502, detail=f"price discovery failed: {exc}"
            ) from exc
        amount = float(offer["amount_usd"]) if offer else 0.0

    base_meta: dict = {"description": req.description, "rail": req.rail}
    if req.rail == "card":
        base_meta["product_handle"] = req.product_handle
        base_meta["variant_id"] = req.variant_id

    try:
        needs_confirmation, reason = svc.verify_within_mandate(
            amount_usd=amount,
            category=req.category,
            mandate=mandate,
            spent_this_month=repo.executed_spend_this_month(),
            vendor=vendor_host(req.target_url),
        )
    except MandateViolation as exc:
        action = repo.create_action(
            req.type, req.target_url, "cancelled", amount,
            {**base_meta, "mandate_violation": str(exc)},
        )
        return {"action": action, "outcome": f"refused: {exc}"}

    expires_at = (
        datetime.now(timezone.utc) + timedelta(minutes=APPROVAL_WINDOW_MINUTES)
    ).isoformat()

    if needs_confirmation:
        action = repo.create_action(
            req.type, req.target_url, "pending_approval", amount,
            {**base_meta, "reason": reason, "expires_at": expires_at},
        )
        return {"action": action, "outcome": f"awaiting approval: {reason}"}

    action = repo.create_action(
        req.type, req.target_url, "approved", amount,
        {**base_meta, "reason": reason},
    )
    if req.rail == "card":
        executed = _execute_card_purchase(action)
    else:
        executed = _execute(action["id"], req.target_url, approved_usd=amount)
    return {"action": executed, "outcome": "executed autonomously (within mandate)"}


@router.post("/{action_id}/approve")
def approve_action(action_id: str) -> dict:
    from db import repositories as repo

    action = repo.get_action(action_id)
    if action is None:
        raise HTTPException(status_code=404, detail="action not found")
    if action["status"] != "pending_approval":
        raise HTTPException(status_code=409, detail=f"action is {action['status']}")

    expires_at = (action.get("metadata") or {}).get("expires_at")
    if expires_at and datetime.fromisoformat(expires_at) < datetime.now(timezone.utc):
        repo.update_action(action_id, {"status": "cancelled",
                                       "metadata": {**action["metadata"], "expired": True}})
        raise HTTPException(status_code=410, detail="approval window expired")

    if action["type"] == "cancel_subscription":
        executed = _execute_cancellation(action)
        return {
            "action": executed,
            "outcome": "approved — cancellation logged; finish it in the "
            "service's account settings (no cancel API exists)",
        }

    if (action.get("metadata") or {}).get("rail") == "card":
        executed = _execute_card_purchase(action)
        return {"action": executed, "outcome": "approved and executed (card rail)"}

    executed = _execute(
        action_id, action["target"], approved_usd=float(action.get("amount_usd") or 0)
    )
    return {"action": executed, "outcome": "approved and executed"}


@router.post("/{action_id}/reject")
def reject_action(action_id: str) -> dict:
    from db import repositories as repo

    action = repo.get_action(action_id)
    if action is None:
        raise HTTPException(status_code=404, detail="action not found")
    if action["status"] != "pending_approval":
        raise HTTPException(status_code=409, detail=f"action is {action['status']}")
    updated = repo.update_action(action_id, {"status": "cancelled"})
    return {"action": updated, "outcome": "rejected"}


@router.get("/bazaar")
def bazaar_search(q: str = "", limit: int = 30) -> list[dict]:
    """Discover real x402-enabled services from the public Bazaar index.
    Free, unauthenticated upstream. Paying a discovered service goes through
    the normal propose flow (mandate rules apply to real money)."""
    from services.bazaar import list_services

    try:
        return list_services(limit=limit, query=q)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"bazaar unreachable: {exc}") from exc


@router.get("/")
def list_actions() -> list[dict]:
    from db import repositories as repo

    try:
        actions = repo.list_actions()
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    # Sweep expired pending approvals on read.
    now = datetime.now(timezone.utc)
    for a in actions:
        exp = (a.get("metadata") or {}).get("expires_at")
        if a["status"] == "pending_approval" and exp and datetime.fromisoformat(exp) < now:
            repo.update_action(a["id"], {"status": "cancelled",
                                         "metadata": {**a["metadata"], "expired": True}})
            a["status"] = "cancelled"
    return actions
