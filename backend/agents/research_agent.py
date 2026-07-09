"""Research agent — orchestrates Reddit retrieval + LLM synthesis.

Handles both Mode 2 (decision synthesis) and Mode 1 (retrospective mining).
The two share retrieval infrastructure but differ in search strategy and
synthesis prompt.
"""

from __future__ import annotations

from core.logging import get_logger
from core.models import OutcomeSummary, ResearchBrief
from services.llm import LLMService
from services.reddit import RedditService

logger = get_logger(__name__)


class ResearchAgent:
    def __init__(
        self, reddit: RedditService | None = None, llm: LLMService | None = None
    ) -> None:
        self._reddit = reddit or RedditService()
        self._llm = llm or LLMService()

    # ---- Mode 2 (Phase 1) ----

    def synthesise_decision(self, query: str, thread_budget: int = 20) -> ResearchBrief:
        """Deep multi-subreddit research → structured consensus brief.

        Steps: identify subreddits → pull threads → signal-filter →
        synthesise. See docs/BUILD_GUIDE.md Phase 1.
        """
        raise NotImplementedError("Phase 1: decision synthesis loop")

    # ---- Mode 1 (Phase 2) ----

    def mine_retrospectives(self, decision: str, context: str = "") -> OutcomeSummary:
        """Find outcome/update posts from people who made this decision.

        Falls back to a low-confidence result (thin_coverage=True) when fewer
        than 5 retrospective posts are found. Never fabricates confidence.
        """
        raise NotImplementedError("Phase 2: retrospective mining loop")
