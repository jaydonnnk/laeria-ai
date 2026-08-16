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


def _guard_http(exc: Exception) -> HTTPException:
    """Turn a guardrail refusal into the right HTTP answer.

    Two different things, two different codes:

    * 400 — Bedrock refused the request. Nothing failed; retrying will refuse
      again. Reported as a plain sentence, never as a server error.
    * 503 — the safety check itself could not run, so the agent stopped instead
      of continuing unverified. Retrying later is exactly right.

    Neither carries a stack trace or an AWS message.
    """
    from services.bedrock_guardrails import GuardrailBlocked, GuardrailUnavailable

    if isinstance(exc, GuardrailBlocked):
        return HTTPException(status_code=400, detail=str(exc))
    if isinstance(exc, GuardrailUnavailable):
        return HTTPException(status_code=503, detail=str(exc))
    return HTTPException(status_code=502, detail=f"research failed: {exc}")


@router.post("/decision")
def start_decision_synthesis(req: DecisionRequest) -> ResearchBrief:
    """Mode 2 — deep multi-subreddit consensus brief. Synchronous."""
    query = f"{req.query} ({req.context})" if req.context else req.query
    try:
        return ResearchAgent().synthesise_decision(query, thread_budget=req.thread_budget)
    except Exception as exc:  # noqa: BLE001
        http = _guard_http(exc)
        if http.status_code == 502:
            logger.exception("decision synthesis failed")
        raise http from exc


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


# ---- live community discovery -------------------------------------------
#
# Reddit refuses automated access from hosted servers and Laeria does not try to
# get around that. Discovery asks a WEB SEARCH provider which discussions exist;
# the backend never sends a request to reddit.com. What the user gets back is a
# list of real, currently-indexed discussions to choose from.


class DiscoverRequest(BaseModel):
    query: str = Field(min_length=3, max_length=500)
    limit: int = Field(default=6, ge=1, le=15)


class DiscoveredThread(BaseModel):
    thread_id: str
    title: str
    url: str
    subreddit: str = ""
    snippet: str = ""
    published: str = ""
    provenance: str = "search_preview"
    relevance: float = 0.0
    preselected: bool = False


@router.post("/discover")
def discover(req: DiscoverRequest) -> dict:
    """Current Reddit discussions for a question, ranked by relevance.

    Cheap and side-effect free by design: one search call, no OpenRouter
    synthesis, no payment, no checkout, and no request to Reddit. It exists so
    the user can see and choose the evidence BEFORE anything expensive runs.

    Every snippet is screened here, because this text is displayed to a human
    and later becomes model context. Guarding it once at the point it enters
    the system is the same rule the rest of the agent follows.
    """
    from agents import relevance
    from agents.research_agent import ResearchAgent
    from services import discovery
    from services.bedrock_guardrails import INPUT

    agent = ResearchAgent()
    query = agent._guard.ensure_allowed(req.query, INPUT, "discovery query")

    source = discovery.get_source()
    ok, detail = source.available()
    if not ok:
        raise HTTPException(status_code=503, detail=detail)

    from core.config import get_settings

    try:
        candidates = source.search(query, limit=get_settings().discovery_candidates)
    except Exception as exc:  # noqa: BLE001
        logger.warning("discovery provider failed: %s", exc)
        raise HTTPException(
            status_code=502, detail="the discovery provider could not be reached"
        ) from exc

    if not candidates:
        return {"query": req.query, "provider": source.name, "results": []}

    # Boundary: search snippets are other people's text on its way to a screen
    # and, later, to a model. A refused snippet costs that one result.
    threads = [discovery.to_thread(c) for c in candidates]
    safe_threads, _ = agent._guard_threads(threads)
    kept = {t.id for t in safe_threads}

    # PR #6 ranking, unchanged — now over title AND snippet, which is the only
    # body text a preview has.
    weights = relevance.build_weights(query, [], safe_threads)
    ranked = sorted(
        (c for c in candidates if c.thread_id in kept),
        key=lambda c: -relevance.score(discovery.to_thread(c), weights),
    )[: req.limit]

    results = [
        DiscoveredThread(
            thread_id=c.thread_id,
            title=c.title,
            url=c.url,
            subreddit=c.subreddit,
            snippet=c.snippet,
            published=c.published,
            relevance=round(relevance.score(discovery.to_thread(c), weights), 2),
            # Pre-tick the strongest few so the flow stays one click, while
            # leaving every choice the user's.
            preselected=i < 3,
        )
        for i, c in enumerate(ranked)
    ]
    return {"query": req.query, "provider": source.name, "results": results}


class SelectedThread(BaseModel):
    thread_id: str = Field(min_length=1, max_length=16)
    title: str = Field(default="", max_length=400)
    url: str = Field(default="", max_length=2048)
    subreddit: str = Field(default="", max_length=64)
    snippet: str = Field(default="", max_length=2000)


class AnalyseRequest(BaseModel):
    query: str = Field(min_length=3, max_length=500)
    threads: list[SelectedThread] = Field(min_length=1, max_length=12)


@router.post("/analyse/submit", status_code=202)
def submit_analyse(
    req: AnalyseRequest, user_id: str = Depends(require_user)
) -> JobAccepted:
    """Research the discussions the user picked. Long-running, so job-based."""
    from services import jobs

    job_id = jobs.submit(
        ResearchAgent().synthesise_selected,
        req.query,
        [t.model_dump() for t in req.threads],
        user_id,
        label=f"analyse: {req.query[:60]}",
    )
    return JobAccepted(job_id=job_id)


# ---- browser-supplied discussions ---------------------------------------


class CapturedComment(BaseModel):
    body: str = Field(default="", max_length=8000)
    author: str = Field(default="", max_length=64)
    score: int | None = None


class IngestRequest(BaseModel):
    """One Reddit discussion, captured from the page the user opened."""

    url: str = Field(min_length=8, max_length=2048)
    title: str = Field(default="", max_length=400)
    body: str = Field(default="", max_length=40_000)
    author: str = Field(default="", max_length=64)
    score: int | None = None
    num_comments: int | None = None
    comments: list[CapturedComment] = Field(default_factory=list, max_length=80)


@router.post("/ingest")
def ingest_thread(req: IngestRequest, user_id: str = Depends(require_user)) -> dict:
    """Accept a Reddit discussion the user chose to send from their browser.

    Laeria never opens this page itself. The user was reading it, decided it
    was relevant, and pressed a button — which is the same act as pasting the
    text in, and is why this exists at all.

    The URL is validated as a real Reddit THREAD and is the only source of the
    thread id and subreddit: a page cannot claim to be a discussion it is not.
    Everything in the body is untrusted and is screened by the same Bedrock
    input boundary as scraped content, because who transported the text says
    nothing about who wrote it.
    """
    from services import discovery, ingest
    from services.bedrock_guardrails import GuardrailBlocked, GuardrailUnavailable

    parsed = discovery.parse_reddit_url(req.url)
    if parsed is None:
        raise HTTPException(
            status_code=422,
            detail="not a Reddit discussion URL (expected a /comments/ link)",
        )
    thread_id, subreddit = parsed

    try:
        thread = ingest.build_thread(req.model_dump(), thread_id, subreddit)
    except ingest.IngestTooLarge as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc

    if not (thread.title or thread.body or thread.top_comments):
        raise HTTPException(status_code=422, detail="no readable content in that page")

    agent = ResearchAgent()
    try:
        safe, _ = agent._guard_threads([thread])
    except GuardrailUnavailable as exc:
        raise _guard_http(exc) from exc
    if not safe:
        raise _guard_http(GuardrailBlocked())

    ingest.put(user_id, thread)
    return {
        "thread_id": thread_id,
        "subreddit": thread.subreddit,
        "title": thread.title,
        "comments_captured": len(thread.top_comments),
        "provenance": discovery.Acquisition.FULL_BROWSER.value,
    }


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
        # This endpoint reaches the planning model directly, and the planner
        # guards its own assembled prompt — so a refusal can arrive from in
        # there and must not surface as "the server broke". The query is not
        # logged: it is user text on a path the guardrail has just inspected.
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
