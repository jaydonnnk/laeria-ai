"""Research routes — Mode 1 (retrospective) and Mode 2 (decision synthesis)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/research", tags=["research"])


class DecisionRequest(BaseModel):
    query: str
    context: str = ""
    thread_budget: int = 20


class RetrospectiveRequest(BaseModel):
    decision: str
    context: str = ""


@router.post("/decision")
def start_decision_synthesis(req: DecisionRequest) -> dict:
    """Mode 2 — deep multi-subreddit consensus brief."""
    raise HTTPException(status_code=501, detail="Phase 1: not implemented")


@router.post("/retrospective")
def start_retrospective(req: RetrospectiveRequest) -> dict:
    """Mode 1 — outcome/update-post mining."""
    raise HTTPException(status_code=501, detail="Phase 2: not implemented")


@router.get("/{session_id}/status")
def get_status(session_id: str) -> dict:
    raise HTTPException(status_code=501, detail="Phase 1: not implemented")


@router.get("/{session_id}/results")
def get_results(session_id: str) -> dict:
    raise HTTPException(status_code=501, detail="Phase 1: not implemented")
