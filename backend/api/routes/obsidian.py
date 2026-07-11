"""Obsidian routes — vault sync for Mode 3 context.

POST /obsidian/sync reads the vault and returns SUGGESTED monitored items.
Nothing is registered automatically — the frontend shows the suggestions and
the user approves each one, which then goes through POST /monitor/items.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from core.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/obsidian", tags=["obsidian"])


@router.post("/sync")
def sync_vault() -> dict:
    """Read vault, infer candidate monitored items for user approval."""
    from services.obsidian import ObsidianService

    svc = ObsidianService()
    if not svc.healthcheck():
        raise HTTPException(
            status_code=503,
            detail="Obsidian is not reachable — is the app open with the "
            "Local REST API plugin enabled?",
        )
    try:
        suggestions = svc.extract_monitored_items()
    except Exception as exc:  # noqa: BLE001
        logger.exception("vault sync failed")
        raise HTTPException(status_code=502, detail=f"vault sync failed: {exc}") from exc
    return {
        "suggestions": [
            {
                "name": s.name,
                "category": s.category,
                "subreddits": s.subreddits,
            }
            for s in suggestions
        ]
    }
