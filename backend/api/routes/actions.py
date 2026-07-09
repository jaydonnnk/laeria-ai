"""Action routes — Phase 4 (x402 + AP2 execution under mandate)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/actions", tags=["actions"])


class ApproveRequest(BaseModel):
    action_id: str


@router.post("/approve")
def approve_action(req: ApproveRequest) -> dict:
    raise HTTPException(status_code=501, detail="Phase 4: not implemented")


@router.get("/")
def list_actions() -> dict:
    raise HTTPException(status_code=501, detail="Phase 4: not implemented")
