"""Obsidian routes — vault sync for Mode 3 context."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/obsidian", tags=["obsidian"])


@router.post("/sync")
def sync_vault() -> dict:
    """Read vault, infer candidate monitored items for user approval."""
    raise HTTPException(status_code=501, detail="Phase 3: not implemented")


@router.get("/items")
def get_vault_items() -> dict:
    raise HTTPException(status_code=501, detail="Phase 3: not implemented")
