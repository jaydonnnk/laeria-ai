"""Research routes — Mode 1 (retrospective) and Mode 2 (decision synthesis).

Phase 1 runs Mode 2 synchronously: the request blocks for the full research
loop (30-90s). FastAPI runs sync handlers in a threadpool, so this doesn't
block the event loop. Async job + status polling is a later refinement.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from agents.research_agent import ResearchAgent
from core.logging import get_logger
from core.models import OutcomeSummary, ResearchBrief

logger = get_logger(__name__)

router = APIRouter(prefix="/research", tags=["research"])


class DecisionRequest(BaseModel):
    query: str = Field(min_length=3, max_length=500)
    context: str = ""
    thread_budget: int = Field(default=8, ge=2, le=20)


class RetrospectiveRequest(BaseModel):
    decision: str = Field(min_length=3, max_length=500)
    context: str = ""
    thread_budget: int = Field(default=8, ge=2, le=20)


@router.post("/decision")
def start_decision_synthesis(req: DecisionRequest) -> ResearchBrief:
    """Mode 2 — deep multi-subreddit consensus brief. Synchronous."""
    query = f"{req.query} ({req.context})" if req.context else req.query
    try:
        return ResearchAgent().synthesise_decision(query, thread_budget=req.thread_budget)
    except Exception as exc:  # noqa: BLE001
        logger.exception("decision synthesis failed")
        raise HTTPException(status_code=502, detail=f"research failed: {exc}") from exc


@router.post("/retrospective")
def start_retrospective(req: RetrospectiveRequest) -> OutcomeSummary:
    """Mode 1 — outcome/update-post mining. Synchronous (2-4 minutes)."""
    try:
        return ResearchAgent().mine_retrospectives(
            req.decision, req.context, thread_budget=req.thread_budget
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("retrospective mining failed")
        raise HTTPException(status_code=502, detail=f"research failed: {exc}") from exc
