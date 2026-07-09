"""Reddit access via PRAW.

Phase 1 implements search + signal filtering. Phase 2 adds retrospective
query strategies. This module is a stub — methods raise NotImplementedError
until then, but connection setup is real so test_environment can verify auth.
"""

from __future__ import annotations

import praw

from core.config import get_settings
from core.logging import get_logger
from core.models import RedditThread

logger = get_logger(__name__)


class RedditService:
    def __init__(self) -> None:
        settings = get_settings()
        self._client = praw.Reddit(
            client_id=settings.reddit_client_id,
            client_secret=settings.reddit_client_secret,
            user_agent=settings.reddit_user_agent,
            username=settings.reddit_username or None,
            password=settings.reddit_password or None,
        )

    def healthcheck(self) -> bool:
        """Fetch a few posts to confirm auth works. Used by test_environment."""
        try:
            posts = list(self._client.subreddit("Singapore").hot(limit=3))
            return len(posts) > 0
        except Exception as exc:  # noqa: BLE001
            logger.error("Reddit healthcheck failed: %s", exc)
            return False

    # ---- Phase 1 ----

    def find_relevant_subreddits(self, topic: str) -> list[str]:
        raise NotImplementedError("Phase 1: uses LLM to suggest subreddits")

    def search_subreddit(
        self, subreddit: str, query: str, time_filter: str = "year", limit: int = 25
    ) -> list[RedditThread]:
        raise NotImplementedError("Phase 1: PRAW search + thread mapping")

    def get_thread_with_comments(self, thread_id: str) -> RedditThread:
        raise NotImplementedError("Phase 1: full thread + top comment fetch")

    def apply_signal_filters(
        self, threads: list[RedditThread]
    ) -> list[RedditThread]:
        """Account age filter, upvote minimums, cross-subreddit corroboration.

        Not optional — build from day one. See docs/SIGNAL_FILTERS.md.
        """
        raise NotImplementedError("Phase 1: signal quality filtering")

    # ---- Phase 2 ----

    def search_retrospective(
        self, topic: str, subreddits: list[str]
    ) -> list[RedditThread]:
        raise NotImplementedError("Phase 2: retrospective/update post search")
