"""Domain models shared across the backend.

These are the canonical shapes for data moving between services, agents,
and the API layer. They mirror the Supabase schema in infra/supabase/schema.sql.
Keep the two in sync manually until a migration tool is introduced.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, model_validator


# ---- Enums ----

class ResearchMode(str, Enum):
    DECISION_SYNTHESIS = "decision_synthesis"   # Mode 2
    RETROSPECTIVE = "retrospective"             # Mode 1


class SignalLevel(str, Enum):
    NONE = "none"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ConfidenceLevel(str, Enum):
    HIGH = "high"
    MODERATE = "moderate"
    LOW = "low"          # thin coverage — say so honestly

    # NOTE: this is a `str` Enum, so comparing members compares their VALUES.
    # Alphabetically "high" < "low" < "moderate", which ranks HIGH weakest.
    # Never order these with `<`; use the explicit ranking in
    # `agents.confidence`.


class EvidenceState(str, Enum):
    """WHY a brief looks the way it does — kept separate from how confident it is.

    "The community disagrees", "we found nothing", and "Reddit is blocked" are
    three different answers that all used to arrive as `confidence: low` with
    the difference buried in English inside `bias_notes`. A UI cannot render a
    distinction the schema does not carry, so it rendered all of them as the
    same red badge — which makes an upstream outage look like a verdict about
    the user's question.
    """

    OK = "ok"                                # a corpus existed and synthesis ran
    NO_EVIDENCE = "no_evidence"              # source reachable, nothing relevant found
    NO_USABLE_EVIDENCE = "no_usable_evidence"  # candidates found, none usable
    SOURCE_UNAVAILABLE = "source_unavailable"  # Reddit itself could not be reached
    NOT_IN_CORPUS = "not_in_corpus"          # frozen corpus has no answer for this query
    # Threads were retrieved and readable, and the safety layer refused every
    # one of them. A distinct state because it has a distinct cause and a
    # distinct honest explanation: nothing failed, and the question is fine —
    # the evidence itself was not safe to read.
    UNSAFE_EVIDENCE = "unsafe_evidence"


class ActionType(str, Enum):
    PURCHASE = "purchase"
    CANCEL_SUBSCRIPTION = "cancel_subscription"
    ORDER_REPLACEMENT = "order_replacement"
    NONE = "none"


class ActionStatus(str, Enum):
    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"
    EXECUTED = "executed"
    CANCELLED = "cancelled"
    FAILED = "failed"


# ---- Reddit primitives ----

class RedditThread(BaseModel):
    id: str
    subreddit: str
    title: str
    body: str = ""
    url: str
    score: int = 0
    num_comments: int = 0
    created_utc: float = 0.0
    author: str = ""
    author_account_age_days: int | None = None
    top_comments: list[str] = Field(default_factory=list)


# ---- Shared: source threads shown to the user ----

class SourceThread(BaseModel):
    """A thread the agent actually read and synthesised from — surfaced in the
    UI so the user can verify the evidence themselves."""
    id: str
    subreddit: str
    title: str
    url: str            # canonical www.reddit.com link
    score: int = 0
    num_comments: int = 0


# ---- Mode 2: Decision synthesis ----

class SignalQuality(BaseModel):
    """The shape of the evidence behind a brief.

    Every field is an observation the pipeline made. Nothing here is inferred,
    estimated, or reconstructed by a client — the frontend renders these, it
    does not compute them.
    """

    # What the planner set out to read. NOT proof that any of these
    # contributed: a subreddit can be searched and yield nothing usable.
    subreddits_checked: list[str] = Field(default_factory=list)
    # Which communities are actually present in the corpus that was
    # synthesised. This is the honest answer to "where did this come from".
    subreddits_represented: list[str] = Field(default_factory=list)

    # Threads fed to synthesis after relevance screening, filtering and
    # duplicate collapsing — the authoritative evidence set. Every other count
    # on this object is measured over the same set.
    usable_thread_count: int = 0
    # Deprecated alias for `usable_thread_count`, always the same value.
    # Retained because existing clients read it; new code should not.
    thread_count: int = 0
    # Of the threads counted above, how many cleared score >= 10 AND
    # comments >= 3 — the bar in RedditService.apply_signal_filters. Measured
    # over the same set as `usable_thread_count`, so it can never exceed it.
    strong_thread_count: int = 0
    # True when `strong_thread_count` fell below the threshold for relying on
    # strong evidence alone, so the broader selection branch was used. Does
    # NOT imply weaker threads existed or were admitted — four strong threads
    # and no weak ones still relaxes.
    filters_relaxed: bool = False

    # Near-identical content under DIFFERENT authors. An uncertainty signal
    # about independence, NOT a finding of manipulation — see
    # docs/SIGNAL_FILTERS.md.
    coordinated_posting_suspected: bool = False
    # Self-crossposts collapsed to their best copy (benign; recorded so the
    # thread count is explainable).
    duplicate_threads_collapsed: int = 0
    # False means the duplicate detector could not run, not that it ran clean.
    similarity_analysis_available: bool = False

    # Whether retrieved threads were screened for relevance to the question
    # before any of them was read. False means the screen could not run, NOT
    # that everything retrieved was relevant.
    relevance_screened: bool = False
    # Search hits dropped as clearly about something else, before any thread
    # was fetched. These never entered any count above.
    off_topic_candidates_rejected: int = 0
    # Model-authored statements about the SHAPE of this evidence that
    # contradicted the authoritative set and were therefore not displayed.
    # Reported rather than hidden: a non-zero value here means the synthesis
    # said something the corpus disproves.
    unverified_claims_removed: int = 0

    # Retrieved threads the Bedrock guardrail refused — prompt injection,
    # credential bait, and the like. They never entered the corpus, so they
    # are counted in none of the numbers above; this field is the only record
    # that they existed.
    unsafe_threads_excluded: int = 0
    # Model-authored strings the guardrail refused on the way out, and so were
    # never displayed.
    guardrail_blocked_outputs: int = 0

    evidence_state: EvidenceState = EvidenceState.OK
    date_range: str = ""
    # Free prose. LLM-authored commentary on sample bias, or a system note on
    # the no-evidence paths. Never parsed — `evidence_state` is the machine
    # -readable channel.
    bias_notes: str = ""


class ResearchBrief(BaseModel):
    consensus_pick: str = ""
    strengths: list[str] = Field(default_factory=list)
    failure_modes: list[str] = Field(default_factory=list)
    what_reviewers_miss: list[str] = Field(default_factory=list)
    alternatives: list[str] = Field(default_factory=list)
    red_flags: list[str] = Field(default_factory=list)

    # The FINAL verdict: min(semantic, structural ceiling). Existing consumers
    # read this field and keep working — it is still "how confident is laeria".
    # Defaults LOW so that any brief built without an explicit decision denies
    # rather than claims; the previous MODERATE default meant a malformed or
    # older payload validated into a middling verdict nobody had computed.
    confidence: ConfidenceLevel = ConfidenceLevel.LOW
    # What the model proposed from reading the threads.
    semantic_confidence: ConfidenceLevel = ConfidenceLevel.LOW
    # The most the evidence SHAPE could justify, whatever the model said.
    structural_ceiling: ConfidenceLevel = ConfidenceLevel.LOW
    # Deterministic, backend-authored explanations for the above. One entry per
    # policy rule that actually fired; empty when nothing limited the verdict.
    confidence_reasons: list[str] = Field(default_factory=list)

    signal_quality: SignalQuality = Field(default_factory=SignalQuality)
    sources: list[SourceThread] = Field(default_factory=list)

    @model_validator(mode="after")
    def _final_confidence_is_bounded(self) -> ResearchBrief:
        """`confidence` cannot outrank its inputs, and cannot survive no pick.

        Both invariants are already enforced by `agents.confidence.assess`, so
        on every live path this is a no-op. It exists because they should hold
        for any brief that can be CONSTRUCTED, not only for the ones this
        version of the pipeline happens to build — a payload deserialised from
        disk, from an older schema, or from a future caller gets the same
        guarantee.

        Three independent rules, applied in order:

        1. No consensus pick forces LOW outright. This is NOT a bound on
           semantic/ceiling and is not derived from them: a brief can have a
           confident model and a clean corpus and still recommend nothing,
           when one half of the split synthesis fails. "High confidence in no
           recommendation" is not a coherent thing to show anyone.
        2. Any evidence state other than OK forces LOW. A blocked source, a
           corpus miss, an empty search and an unfetchable corpus all mean the
           same thing for confidence: there is no evidence here, so nothing
           can authorise a HIGH or MODERATE verdict about it.
        3. Whatever survives is then clamped to the weakest of the three
           confidence values.

        Only `confidence` is touched. `semantic_confidence` and
        `structural_ceiling` are left exactly as they were, because they are
        the honest record of what each layer decided — rewriting them would
        destroy the very explanation the UI needs to tell the user why the
        final verdict is what it is.

        Clamps rather than raises. The decision-cache read has no handler
        around it, so raising would turn one malformed entry into a failed
        research run; degrading to the most conservative defensible value is
        both safer and the direction this codebase fails in everywhere else.
        """
        final = self.confidence
        if not self.consensus_pick.strip():
            final = ConfidenceLevel.LOW
        if self.signal_quality.evidence_state is not EvidenceState.OK:
            final = ConfidenceLevel.LOW
        final = min(
            final,
            self.semantic_confidence,
            self.structural_ceiling,
            key=_CONFIDENCE_RANK.__getitem__,
        )
        if final is not self.confidence:
            object.__setattr__(self, "confidence", final)
        return self


# Ranking for the clamp above. Separate from `agents.confidence` on purpose:
# `core.models` is imported by that module, and importing back would be a
# cycle. Three lines duplicated is cheaper than the coupling.
_CONFIDENCE_RANK: dict[ConfidenceLevel, int] = {
    ConfidenceLevel.LOW: 0,
    ConfidenceLevel.MODERATE: 1,
    ConfidenceLevel.HIGH: 2,
}


# ---- Mode 1: Retrospective outcomes ----

class OutcomeSummary(BaseModel):
    retrospective_count: int = 0    # outcome reports identified by the classifier
    threads_read: int = 0           # of those, read in full — the split is based on THESE
    pct_positive: float = 0.0
    pct_negative: float = 0.0
    pct_mixed: float = 0.0
    common_positives: list[str] = Field(default_factory=list)
    common_regrets: list[str] = Field(default_factory=list)
    surprising_findings: list[str] = Field(default_factory=list)
    sample_bias: str = ""
    confidence: ConfidenceLevel = ConfidenceLevel.MODERATE
    thin_coverage: bool = False
    sources: list[SourceThread] = Field(default_factory=list)


# ---- Mode 3: Monitoring ----

class ActionMandate(BaseModel):
    """Standing spending rules.

    An unset cap means NO ALLOWANCE, not unlimited. `None` is the default so
    that a missing profile row — `ActionMandate(**{})` — denies everything
    rather than authorising unbounded autonomous spend. A cap of 0.0 is
    likewise zero allowance; there is deliberately no way to express
    "unlimited" in this model.
    """

    max_per_transaction: float | None = None
    max_per_month: float | None = None
    require_confirmation_above: float | None = None
    allowed_categories: list[str] = Field(default_factory=list)
    blocked_vendors: list[str] = Field(default_factory=list)
    autonomous_actions_enabled: bool = False


class MonitoredItem(BaseModel):
    id: str | None = None
    user_id: str
    name: str
    category: str = ""
    subreddits: list[str] = Field(default_factory=list)
    check_interval_hours: int = 6
    action_mandate: ActionMandate = Field(default_factory=ActionMandate)
    active: bool = True
    created_at: datetime | None = None


class MonitorAlert(BaseModel):
    id: str | None = None
    item_id: str
    run_id: str | None = None
    severity: SignalLevel
    summary: str
    thread_urls: list[str] = Field(default_factory=list)
    recommended_action: ActionType = ActionType.NONE
    actioned: bool = False
    created_at: datetime | None = None


# ---- Actions (Phase 4) ----

class Action(BaseModel):
    id: str | None = None
    user_id: str
    type: ActionType
    target: str
    status: ActionStatus = ActionStatus.PENDING_APPROVAL
    amount_usd: float = 0.0
    mandate_ref: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime | None = None
