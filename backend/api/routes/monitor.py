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
    try:
        return check_item(row)
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
