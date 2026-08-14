"""Research routes — Mode 1 (retrospective) and Mode 2 (decision synthesis).

Two shapes for each mode:

* Synchronous (`/decision`, `/retrospective`) — blocks for the whole research
  loop. Fine locally and from scripts; Mode 1 runs 2-4 minutes and will 504
  behind any normal proxy.
* Job-based (`/decision/submit`, `/retrospective/submit`, `/jobs/{id}`) —
  returns immediately with a job id to poll. This is what a browser should
  use over the public internet.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from agents.research_agent import ResearchAgent
from core.auth import require_user
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


class ActOnBriefRequest(BaseModel):
    """Mode 2 trigger: execute a purchase off a research brief."""
    query: str = Field(min_length=3, max_length=500)
    consensus_pick: str = Field(min_length=3, max_length=600)
    confidence: str = Field(pattern="^(high|moderate|low)$")


@router.post("/act", dependencies=[Depends(require_user)])
def act_on_brief(req: ActOnBriefRequest) -> dict:
    """The plan's core promise: consensus strong -> agent buys. Server-side
    integrity gate — refuses to act on weak research regardless of what the
    UI sends. The purchase itself goes through the same mandate/approval
    pipeline as any other action.

    Signed-in, not owner-only: this endpoint spends, but it now spends the
    caller's OWN per-user agent wallet through the mandate pipeline, exactly
    like the Commerce and Actions routes. Gating it to the owner while those
    are open to everyone would 403 a normal user's 'Worth it? -> act' while
    letting the same purchase through on the Commerce page.
    """
    if req.confidence == "low":
        raise HTTPException(
            status_code=409,
            detail="refusing to act on low-confidence research — the whole "
            "point is not spending money on weak signal",
        )

    from api.routes.actions import ProposeRequest, propose_action
    from core.config import get_settings

    return propose_action(
        ProposeRequest(
            type="purchase",
            target_url=get_settings().action_vendor_url,
            category="research",
            description=f"[Mode 2] {req.consensus_pick[:200]} (query: {req.query[:120]})",
        )
    )


@router.get("/subreddits")
def suggest_subreddits(q: str) -> dict:
    """Which communities a research run would read for this query.

    Exists for the extension's order import. `POST /monitor/items` requires at
    least one subreddit, and asking someone to name communities for a thing
    they just bought is exactly where they abandon the flow. Reusing the
    research planner means a monitored item watches the same places the
    research would have read, rather than a guess made client-side.

    Cheap by comparison with research itself — one small LLM call, or a
    fixture hit when the corpus has a recorded plan for this query.
    """
    if len(q.strip()) < 3:
        raise HTTPException(status_code=422, detail="q must be at least 3 characters")
    try:
        plan = ResearchAgent()._identify_subreddits(q.strip())
    except Exception as exc:  # noqa: BLE001
        logger.warning("subreddit suggestion failed for %r: %s", q, exc)
        raise HTTPException(status_code=502, detail=f"could not plan: {exc}") from exc
    return {
        "subreddits": plan["subreddits"][:6],
        "search_queries": plan["search_queries"],
    }


@router.post("/retrospective")
def start_retrospective(req: RetrospectiveRequest) -> OutcomeSummary:
    """Mode 1 — outcome/update-post mining. Synchronous (2-4 minutes).

    Kept for local use and scripts. Over the public internet this exceeds
    every common proxy timeout (Cloudflare cuts at 100s) — use the job
    endpoints below instead.
    """
    try:
        return ResearchAgent().mine_retrospectives(
            req.decision, req.context, thread_budget=req.thread_budget
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("retrospective mining failed")
        raise HTTPException(status_code=502, detail=f"research failed: {exc}") from exc


# ---- job-based variants -------------------------------------------------
#
# Research runs far longer than any proxy will hold a connection open, so the
# request returns a job id and the client polls. See services/jobs.


class JobAccepted(BaseModel):
    job_id: str
    status: str = "pending"


@router.post("/decision/submit", status_code=202)
def submit_decision(req: DecisionRequest) -> JobAccepted:
    from services import jobs

    query = f"{req.query} ({req.context})" if req.context else req.query
    job_id = jobs.submit(
        ResearchAgent().synthesise_decision,
        query,
        thread_budget=req.thread_budget,
        label=f"decision: {req.query[:60]}",
    )
    return JobAccepted(job_id=job_id)


@router.post("/retrospective/submit", status_code=202)
def submit_retrospective(req: RetrospectiveRequest) -> JobAccepted:
    from services import jobs

    job_id = jobs.submit(
        ResearchAgent().mine_retrospectives,
        req.decision,
        req.context,
        thread_budget=req.thread_budget,
        label=f"retrospective: {req.decision[:60]}",
    )
    return JobAccepted(job_id=job_id)


@router.get("/jobs/{job_id}")
def get_job(job_id: str) -> dict:
    """Poll a submitted research job.

    status is pending | running | done | error. `result` carries the brief or
    summary once done; `error` carries the failure message. `elapsed_seconds`
    is included so a client can show honest progress rather than a dead
    spinner.
    """
    from services import jobs

    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="unknown or expired job")

    result = job.get("result")
    if hasattr(result, "model_dump"):
        result = result.model_dump(mode="json")
    return {
        "job_id": job_id,
        "status": job["status"],
        "label": job.get("label", ""),
        "elapsed_seconds": job.get("elapsed_seconds", 0),
        "result": result,
        "error": job.get("error"),
    }
