"""Monitor routes — Mode 3 (ongoing subreddit watching)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/monitor", tags=["monitor"])


class CreateItemRequest(BaseModel):
    name: str
    category: str = ""
    subreddits: list[str] = []
    check_interval_hours: int = 6


@router.post("/items")
def create_item(req: CreateItemRequest) -> dict:
    raise HTTPException(status_code=501, detail="Phase 3: not implemented")


@router.get("/items")
def list_items() -> dict:
    raise HTTPException(status_code=501, detail="Phase 3: not implemented")


@router.delete("/items/{item_id}")
def delete_item(item_id: str) -> dict:
    raise HTTPException(status_code=501, detail="Phase 3: not implemented")


@router.get("/alerts")
def list_alerts() -> dict:
    raise HTTPException(status_code=501, detail="Phase 3: not implemented")
