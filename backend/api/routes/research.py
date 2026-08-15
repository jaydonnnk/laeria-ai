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
from pydantic import BaseModel, Field, ValidationError

from agents.research_agent import ResearchAgent, effective_query
from core.auth import require_owner
from core.logging import get_logger
from core.models import ConfidenceLevel, OutcomeSummary, ResearchBrief

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


def _guard_http(exc: Exception) -> HTTPException:
    """Turn a guardrail refusal into the right HTTP answer.

    Two different things, deliberately given two different codes:

    * 400 — Bedrock refused the request. Nothing failed; retrying will refuse
      again. Reported as a plain sentence, never as a server error, because
      "something broke" would be a lie and would invite a retry.
    * 503 — the safety check itself could not run, so the agent stopped
      instead of continuing unverified. Retrying later is exactly right.

    Neither carries a stack trace or an AWS message: the caller gets the
    sentence the service wrote for them.
    """
    from services.bedrock_guardrails import GuardrailBlocked, GuardrailUnavailable

    if isinstance(exc, GuardrailBlocked):
        return HTTPException(status_code=400, detail=str(exc))
    if isinstance(exc, GuardrailUnavailable):
        return HTTPException(status_code=503, detail=str(exc))
    return HTTPException(status_code=502, detail=f"research failed: {exc}")


def _guard_query(query: str) -> str:
    """Check a research question, and return the text that may be used.

    RETURNS THE GUARDED TEXT, and callers must use it. When the guardrail
    masks personal data out of a question, the masked form is the only version
    that may travel onward; throwing the return value away and carrying on with
    the original would pass the check and then leak anyway.

    For the two `submit` endpoints this is an EARLY rejection only — the agent
    checks the query again for itself, and that is the boundary that actually
    protects the pipeline, because it holds for every caller including the
    worker and the browser extension. This earlier check exists so the person
    typing gets an immediate, clearly-worded answer instead of a background job
    that fails a minute later.

    For `/research/subreddits` there is no second check: that endpoint reaches
    the planning model directly, so this IS its boundary.
    """
    from services.bedrock_guardrails import INPUT, get_guardrails

    try:
        return get_guardrails().ensure_allowed(query, INPUT, "research query")
    except Exception as exc:  # noqa: BLE001
        raise _guard_http(exc) from exc


@router.post("/decision")
def start_decision_synthesis(req: DecisionRequest) -> ResearchBrief:
    """Mode 2 — deep multi-subreddit consensus brief. Synchronous."""
    query = effective_query(req.query, req.context)
    try:
        return ResearchAgent().synthesise_decision(query, thread_budget=req.thread_budget)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        http = _guard_http(exc)
        if http.status_code == 502:
            logger.exception("decision synthesis failed")
        raise http from exc


class ActOnBriefRequest(BaseModel):
    """Mode 2 trigger: execute a purchase off a research brief.

    Carries only what is needed to IDENTIFY the research run — never its
    conclusions. Earlier versions accepted `confidence` and `consensus_pick`
    from the caller, which meant the server's "integrity gate" was reading the
    verdict from the same client it was supposed to be checking. Both are now
    read from the stored brief instead.

    Compatibility with a pre-Phase-A client is PARTIAL, and deliberately so:

    * Legacy `confidence` / `consensus_pick` keys are ignored rather than
      rejected (pydantic's default), so posting them is harmless.
    * A legacy request for a decision made WITHOUT context still works: the
      effective query is the bare query, which is what such a client sends.
    * A legacy request for a CONTEXT-BEARING decision fails closed with 409.
      The brief was stored under "query (context)" and the old client sends no
      context, so its authoritative entry cannot be located.

    That last case is a refusal, not a bug. Loosening the lookup to find a
    brief by query alone would let one question act on another question's
    research, which is the exact confusion the exact-key lookup exists to
    prevent. It resolves when the updated frontend sends `context`.
    """

    query: str = Field(min_length=3, max_length=500)
    # Required for the lookup: /decision researches "query (context)", so a
    # context-bearing decision is stored under that composed string and bare
    # `query` would miss its own brief.
    context: str = ""


@router.post("/act", dependencies=[Depends(require_owner)])
def act_on_brief(req: ActOnBriefRequest) -> dict:
    """The plan's core promise: consensus strong -> agent buys.

    The confidence and the pick are read from the brief this server computed
    and stored, never from the request. A caller cannot promote its own
    research by claiming a level, and cannot redirect the purchase by naming a
    product the research never recommended — neither value is an input.

    Fails closed: if there is no authoritative brief for this exact query, the
    answer is "run the research first", not "proceed on the caller's word".

    Owner-only despite living on the research router: this is the one research
    endpoint that spends, and it spends from the single shared agent wallet.
    The purchase itself still goes through the unchanged mandate/approval
    pipeline — research confidence decides whether to ASK, the mandate decides
    whether it may happen.
    """
    from core.config import get_settings
    from services import research_cache

    ttl = get_settings().research_cache_seconds
    if ttl <= 0:
        # The decision cache doubles as this endpoint's source of authority,
        # and a non-positive TTL disables reads entirely. Telling the user to
        # "run the research again" would be advice that cannot work: the brief
        # would be written and then be unreadable, forever. Name the real
        # cause instead.
        #
        # This coupling is a known limitation, not a design: durable action
        # authority and optional result caching want to be separate stores.
        # Out of scope here — see the deferred findings.
        raise HTTPException(
            status_code=409,
            detail="server-side research authority is disabled "
            "(RESEARCH_CACHE_SECONDS=0), so acting on a stored brief cannot "
            "work in this configuration — set a positive TTL to enable "
            "acting on research",
        )

    query = effective_query(req.query, req.context)
    cached = research_cache.get(
        query, kind=research_cache.DECISION_CACHE_KIND, ttl_seconds=ttl
    )
    if cached is None:
        raise HTTPException(
            status_code=409,
            detail="no current research brief for this query — run the research "
            "again, then act on the result",
        )

    try:
        brief = ResearchBrief.model_validate(cached)
    except ValidationError as exc:
        # An entry that cannot be read as a brief is not an authority.
        logger.warning("unreadable cached brief for %r: %s", query[:80], exc)
        raise HTTPException(
            status_code=409,
            detail="the stored research brief could not be read — run the "
            "research again",
        ) from exc

    if brief.confidence is ConfidenceLevel.LOW:
        raise HTTPException(
            status_code=409,
            detail="refusing to act on low-confidence research — the whole "
            "point is not spending money on weak signal",
        )
    if not brief.consensus_pick.strip():
        # Unreachable while the confidence policy forces LOW without a pick,
        # kept because "buy the nothing it recommended" must never depend on
        # another module's rule staying the way it is today.
        raise HTTPException(
            status_code=409,
            detail="the research produced no consensus pick to act on",
        )

    from api.routes.actions import ProposeRequest, propose_action

    return propose_action(
        ProposeRequest(
            type="purchase",
            target_url=get_settings().action_vendor_url,
            category="research",
            description=f"[Mode 2] {brief.consensus_pick[:200]} (query: {query[:120]})",
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
    # This endpoint reaches the planning LLM directly, without going through
    # `synthesise_decision`, so it needs the input boundary of its own.
    safe_q = _guard_query(q.strip())
    try:
        plan = ResearchAgent()._identify_subreddits(safe_q)
    except Exception as exc:  # noqa: BLE001
        # The planner checks its own assembled prompt, so a refusal can also
        # arrive from in there. Routed through the same mapper as everywhere
        # else, or it would surface as "the server broke" rather than "this was
        # refused". The query is not logged: it is user text, and this line
        # fires on the path where the guardrail has just had reason to look
        # at it.
        http = _guard_http(exc)
        if http.status_code == 502:
            logger.warning("subreddit suggestion failed: %s", exc)
            http = HTTPException(status_code=502, detail=f"could not plan: {exc}")
        raise http from exc
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
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        http = _guard_http(exc)
        if http.status_code == 502:
            logger.exception("retrospective mining failed")
        raise http from exc


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

    query = effective_query(req.query, req.context)
    # Refuse here rather than inside the job, so a blocked question comes back
    # as an immediate answer instead of a spinner that ends in an error.
    _guard_query(query)
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

    _guard_query(effective_query(req.decision, req.context))
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
