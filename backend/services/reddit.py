"""Reddit access via old.reddit.com HTML.

Why not PRAW / the JSON API: as of Reddit's late-2025 Responsible Builder
Policy, the free Data API (OAuth script apps) is gated behind a manual approval
form that favours moderation/commercial use cases — not ours. The public
`.json` endpoints now return 403 to unauthenticated clients. Plain HTML pages
(especially old.reddit.com) still serve 200 and carry fully parseable post
data, so that's the Phase-1 data source.

This is a deliberate, documented tradeoff for a personal non-commercial build.
The public methods below form a stable interface (`search_subreddit`,
`get_thread_with_comments`, ...) so the data source can later be swapped to a
sanctioned third-party provider (Apify/Data365/etc.) without touching agents.

Phase 0.4 implements connection + healthcheck only. Search / thread fetch /
signal filtering are Phase 1 — stubbed with NotImplementedError below.
"""

from __future__ import annotations

import httpx

from core.config import get_settings
from core.logging import get_logger
from core.models import RedditThread

logger = get_logger(__name__)

# old.reddit serves 200 to browser-like clients; a bot-style UA gets 403.
_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
_BASE = "https://old.reddit.com"


class RedditService:
    def __init__(self) -> None:
        settings = get_settings()
        # reddit_user_agent kept in config for a future provider; HTML scraping
        # needs a browser UA, so we don't use the bot-style one here.
        self._settings = settings
        self._client = httpx.Client(
            base_url=_BASE,
            headers={"User-Agent": _BROWSER_UA},
            timeout=15.0,
            follow_redirects=True,
        )

    def _get(self, path: str, params: dict | None = None) -> httpx.Response:
        """Single GET against old.reddit with the browser UA. Raises on non-200
        so callers/healthcheck see failures explicitly."""
        resp = self._client.get(path, params=params)
        resp.raise_for_status()
        return resp

    def healthcheck(self) -> bool:
        """Fetch a subreddit listing and confirm parseable posts come back.
        Used by test_environment (Phase 0.4)."""
        try:
            resp = self._get("/r/Singapore/")
            # old.reddit tags each post link with data-fullname="t3_..."
            return 'data-fullname="t3_' in resp.text
        except Exception as exc:  # noqa: BLE001
            logger.error("Reddit healthcheck failed: %s", exc)
            return False

    # ---- Phase 1 ----

    def find_relevant_subreddits(self, topic: str) -> list[str]:
        raise NotImplementedError("Phase 1: uses LLM to suggest subreddits")

    def search_subreddit(
        self, subreddit: str, query: str, time_filter: str = "year", limit: int = 25
    ) -> list[RedditThread]:
        raise NotImplementedError(
            "Phase 1: parse old.reddit /r/{sub}/search HTML -> RedditThread"
        )

    def get_thread_with_comments(self, thread_id: str) -> RedditThread:
        raise NotImplementedError(
            "Phase 1: parse old.reddit /comments/{id} HTML -> thread + top comments"
        )

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
