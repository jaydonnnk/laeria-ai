"""Monitor routes — Mode 3 (ongoing subreddit watching).

CRUD over monitored items and alerts, plus a synchronous run-now check that
shares the worker's code path. Single-user mode: everything is scoped to
OWNER_USER_ID by the repository layer.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from core.logging import get_logger
from core.models import MonitoredItem

logger = get_logger(__name__)

router = APIRouter(prefix="/monitor", tags=["monitor"])


class CreateItemRequest(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    category: str = ""
    subreddits: list[str] = Field(min_length=1, max_length=6)
    check_interval_hours: int = Field(default=6, ge=1, le=168)


@router.post("/items")
def create_item(req: CreateItemRequest) -> dict:
    from db import repositories as repo
    from services.bedrock_guardrails import (
        INPUT,
        GuardrailBlocked,
        GuardrailUnavailable,
        get_guardrails,
    )

    # EARLY REJECTION, NOT THE BOUNDARY.
    #
    # A refused name is worth catching here so the user is told immediately
    # instead of creating an item that quietly fails every check. But this is
    # not what protects the model: `AlertEngine.classify_run` checks the name
    # again at the moment it would enter a prompt, which is the only place that
    # also covers rows created before this integration existed, seeded or
    # imported items, and internal callers.
    #
    # The RETURN VALUE IS DELIBERATELY DISCARDED, which is the opposite of the
    # rule everywhere else in this codebase. Everywhere else the guarded text
    # is about to be sent to a model, so using the original would leak. Here
    # nothing is being sent — the name is being STORED. It is the user's own
    # word for their own subscription, and rewriting "alerts for me@x.com" into
    # "alerts for {EMAIL}" in their dashboard would corrupt their data to solve
    # a problem the sending side already solves. Masked at the model boundary,
    # intact in the database.
    try:
        get_guardrails().ensure_allowed(req.name, INPUT, "monitored item name")
    except GuardrailBlocked as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except GuardrailUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    item = MonitoredItem(
        user_id="",  # repository injects the owner id
        name=req.name,
        category=req.category,
        subreddits=[s.strip().removeprefix("r/") for s in req.subreddits if s.strip()],
        check_interval_hours=req.check_interval_hours,
    )
    try:
        return repo.create_item(item)
    except RuntimeError as exc:  # OWNER_USER_ID missing
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/items")
def list_items() -> list[dict]:
    from db import repositories as repo

    try:
        items = repo.list_items()
    except RuntimeError as exc:  # OWNER_USER_ID missing
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    # Attach recent run signal history for the dashboard dots.
    for item in items:
        runs = repo.recent_runs(item["id"], limit=10)
        item["recent_signals"] = [
            {"signal_level": r["signal_level"], "ran_at": r["ran_at"],
             "posts_found": r["posts_found"]}
            for r in runs
        ]
    return items


@router.delete("/items/{item_id}")
def delete_item(item_id: str) -> dict:
    from db import repositories as repo

    if not repo.delete_item(item_id):
        raise HTTPException(status_code=404, detail="item not found")
    return {"deleted": item_id}


@router.post("/items/{item_id}/check")
def check_now(item_id: str) -> dict:
    """Run one monitoring check synchronously (30-60s). Same code path as
    the background worker."""
    from db import repositories as repo
    from workers.monitor_worker import check_item

    row = repo.get_item(item_id)
    if row is None:
        raise HTTPException(status_code=404, detail="item not found")
    from services.bedrock_guardrails import GuardrailBlocked, GuardrailUnavailable

    try:
        return check_item(row)
    except GuardrailUnavailable as exc:
        # The safety check could not run, so the check stopped rather than
        # classifying unverified posts. 503 says "try again", which is true.
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except GuardrailBlocked as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        logger.exception("run-now check failed")
        raise HTTPException(status_code=502, detail=f"check failed: {exc}") from exc


@router.get("/alerts")
def list_alerts() -> list[dict]:
    from db import repositories as repo

    try:
        return repo.list_alerts()
    except RuntimeError as exc:  # OWNER_USER_ID missing
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/alerts/{alert_id}/dismiss")
def dismiss_alert(alert_id: str) -> dict:
    from db import repositories as repo

    repo.mark_alert_actioned(alert_id)
    return {"dismissed": alert_id}
