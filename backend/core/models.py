"""Domain models shared across the backend.

These are the canonical shapes for data moving between services, agents,
and the API layer. They mirror the Supabase schema in infra/supabase/schema.sql.
Keep the two in sync manually until a migration tool is introduced.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


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
    subreddits_checked: list[str] = Field(default_factory=list)
    thread_count: int = 0
    date_range: str = ""
    bias_notes: str = ""


class ResearchBrief(BaseModel):
    consensus_pick: str = ""
    failure_modes: list[str] = Field(default_factory=list)
    what_reviewers_miss: list[str] = Field(default_factory=list)
    alternatives: list[str] = Field(default_factory=list)
    red_flags: list[str] = Field(default_factory=list)
    confidence: ConfidenceLevel = ConfidenceLevel.MODERATE
    signal_quality: SignalQuality = Field(default_factory=SignalQuality)
    sources: list[SourceThread] = Field(default_factory=list)


# ---- Mode 1: Retrospective outcomes ----

class OutcomeSummary(BaseModel):
    retrospective_count: int = 0
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
    max_per_transaction: float = 0.0
    max_per_month: float = 0.0
    require_confirmation_above: float = 0.0
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
