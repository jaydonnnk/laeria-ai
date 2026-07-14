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
    max_per_transaction: float = Field(default=0, ge=0)
    max_per_month: float = Field(default=0, ge=0)
    require_confirmation_above: float = Field(default=0, ge=0)
    allowed_categories: list[str] = []
    blocked_vendors: list[str] = []
    autonomous_actions_enabled: bool = False


class ProposeRequest(BaseModel):
    type: str = Field(pattern="^(purchase|cancel_subscription|order_replacement)$")
    target_url: str = Field(min_length=8, max_length=500)
    category: str = ""
    description: str = ""


def _mandate() -> ActionMandate:
    from db import repositories as repo

    return ActionMandate(**repo.get_mandate())


def _execute(action_id: str, target_url: str) -> dict:
    """Pay the target and mark the action executed. Failure marks it failed."""
    from db import repositories as repo
    from services.payment import PaymentService

    try:
        result = PaymentService().pay_402(target_url)
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

    # Price discovery: what does the target actually cost?
    offer = svc.check_402(req.target_url)
    amount = float(offer["amount_usd"]) if offer else 0.0

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
            {"description": req.description, "mandate_violation": str(exc)},
        )
        return {"action": action, "outcome": f"refused: {exc}"}

    expires_at = (
        datetime.now(timezone.utc) + timedelta(minutes=APPROVAL_WINDOW_MINUTES)
    ).isoformat()

    if needs_confirmation:
        action = repo.create_action(
            req.type, req.target_url, "pending_approval", amount,
            {"description": req.description, "reason": reason, "expires_at": expires_at},
        )
        return {"action": action, "outcome": f"awaiting approval: {reason}"}

    action = repo.create_action(
        req.type, req.target_url, "approved", amount,
        {"description": req.description, "reason": reason},
    )
    executed = _execute(action["id"], req.target_url)
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

    executed = _execute(action_id, action["target"])
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
