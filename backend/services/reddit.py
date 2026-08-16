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

Markup notes (verified live 2026-07-11):
- Search page: `div.search-result-link` with `data-fullname="t3_..."`,
  `a.search-title`, `a.search-comments`, `time[datetime]`, `a.author`.
  Search-page scores are unreliable (often "0 points") — real scores come from
  the thread page's `data-score`.
- Thread page: `div.thing.link` carries data-fullname/-subreddit/-score/
  -comments-count/-author/-timestamp; selftext in `div.expando
  div.usertext-body`. Comments: `div.commentarea > div.sitetable >
  div.thing.comment` (top-level), body in `div.usertext-body`, score in
  `span.score.unvoted`.

Signal-filter limitation vs the API: author account age is NOT available in
listing/thread HTML (would cost one extra request per author). Phase 1 filters
therefore lean on upvote thresholds, comment volume, and cross-subreddit
corroboration; account-age filtering is deferred to the provider swap.
"""

from __future__ import annotations

import re
import time

import httpx
from bs4 import BeautifulSoup
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

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

# Politeness: minimum gap between requests. old.reddit tolerates slow browsers;
# hammering it invites the 403 wall for the whole IP.
_MIN_REQUEST_GAP_SECONDS = 1.5


class FixtureMissing(RuntimeError):
    """No recorded answer exists for this request in `fixture` mode.

    Named rather than a bare RuntimeError because callers must treat it
    exactly like an upstream failure: a subreddit with no captured search is
    indistinguishable, from the caller's point of view, from one that 403s.
    Left unnamed it escaped the `except httpx.HTTPStatusError` guards and took
    down an entire research run over one uncaptured subreddit -- which is the
    difference between `fixture` mode being the safe demo setting and being a
    liability.
    """


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in (429, 500, 502, 503, 504)
    return isinstance(exc, (httpx.TimeoutException, httpx.TransportError))


class RedditService:
    def __init__(self) -> None:
        settings = get_settings()
        # reddit_user_agent kept in config for a future provider; HTML scraping
        # needs a browser UA, so we don't use the bot-style one here.
        self._settings = settings
        # Route through a proxy when configured (needed on datacenter IPs that
        # Reddit blocks; unnecessary on a residential connection).
        proxy = settings.reddit_proxy or None
        if proxy:
            logger.info("Reddit requests routed through configured proxy")
        self._client = httpx.Client(
            base_url=_BASE,
            headers={"User-Agent": _BROWSER_UA},
            timeout=15.0,
            follow_redirects=True,
            proxy=proxy,
        )

    # ---- HTTP layer ----

    # Pacing state is CLASS-level and lock-guarded: concurrent research runs
    # each create their own RedditService, and per-instance pacing would let
    # them stack up to a combined request rate Reddit flags. One shared gap
    # for the whole process.
    _pace_lock = __import__("threading").Lock()
    _last_request_at = 0.0

    def _pace(self) -> None:
        """Reserve this request's slot in the process-wide rate limit.

        The sleep happens OUTSIDE the lock, and that is the whole point. Held
        across the sleep, the lock serialises every caller: each request then
        costs the full gap PLUS its own latency, so eight thread fetches cost
        eight times the sum rather than eight gaps. Reserving a slot and
        waiting for it lets those latencies overlap while the rate Reddit
        actually observes is unchanged — still one request per gap, still one
        shared cursor for the whole process.
        """
        with RedditService._pace_lock:
            slot = max(
                time.monotonic(),
                RedditService._last_request_at + _MIN_REQUEST_GAP_SECONDS,
            )
            RedditService._last_request_at = slot
        delay = slot - time.monotonic()
        if delay > 0:
            time.sleep(delay)

    @retry(
        retry=retry_if_exception(_is_retryable),
        wait=wait_exponential(multiplier=2, min=2, max=30),
        stop=stop_after_attempt(4),
        reraise=True,
    )
    def _fetch_live(self, path: str, params: dict | None = None) -> httpx.Response:
        """Paced GET against old.reddit. Retries transient failures with
        backoff; raises on hard failures (403 = blocked, don't hammer)."""
        self._pace()
        resp = self._client.get(path, params=params)
        from core.usage import incr

        incr("reddit_requests")
        resp.raise_for_status()
        return resp

    @staticmethod
    def _replayed(path: str, params: dict | None, body: str) -> httpx.Response:
        """Wrap a recorded body so callers can't tell it from a live fetch."""
        return httpx.Response(
            200,
            text=body,
            request=httpx.Request("GET", f"{_BASE}{path}", params=params),
        )

    def _get(self, path: str, params: dict | None = None) -> httpx.Response:
        """Fetch, replay, or fetch-then-replay depending on REDDIT_SOURCE.

        Reddit is closing logged-out old.reddit access (announced 2026-06-30,
        rolling out across the month), so a live fetch is no longer something
        the demo can assume. See services/reddit_fixtures for the modes.
        """
        from services import reddit_fixtures as fx

        mode = self._settings.reddit_source
        if mode not in fx.VALID_MODES:
            raise RuntimeError(
                f"REDDIT_SOURCE={mode!r} is not one of {sorted(fx.VALID_MODES)}"
            )

        def replay_or_none() -> httpx.Response | None:
            """Recorded outcome for this request: body, recorded failure, or
            nothing. A recorded failure is re-raised so a replayed run follows
            the same branches as the run it was captured from."""
            status = fx.load_error(path, params)
            if status is not None:
                request = httpx.Request("GET", f"{_BASE}{path}", params=params)
                raise httpx.HTTPStatusError(
                    f"{status} (recorded at capture time)",
                    request=request,
                    response=httpx.Response(status, request=request),
                )
            body = fx.load(path, params)
            return self._replayed(path, params, body) if body is not None else None

        if mode == "fixture":
            replayed = replay_or_none()
            if replayed is None:
                raise FixtureMissing(
                    f"no fixture for {path} {params or ''} — capture one with "
                    "`python -m scripts.capture_corpus` while live access works"
                )
            return replayed

        try:
            resp = self._fetch_live(path, params)
        except httpx.HTTPStatusError as exc:
            if mode == "record":
                # Record the failure too: a corpus that only contains successes
                # cannot reproduce a run that handled a 403 partway through.
                fx.save_error(path, params, exc.response.status_code)
                raise
            if mode != "live_then_fixture":
                raise
            replayed = replay_or_none()
            if replayed is None:
                logger.error("live Reddit fetch failed and no fixture exists for %s", path)
                raise
            logger.warning(
                "live Reddit fetch failed (%s) — replaying fixture for %s", exc, path
            )
            return replayed
        except Exception as exc:  # noqa: BLE001
            if mode != "live_then_fixture":
                raise
            replayed = replay_or_none()
            if replayed is None:
                logger.error("live Reddit fetch failed and no fixture exists for %s", path)
                raise
            logger.warning(
                "live Reddit fetch failed (%s) — replaying fixture for %s", exc, path
            )
            return replayed

        if mode == "record":
            fx.save(path, params, resp.text)
        return resp

    def probe_live(self) -> tuple[bool, str]:
        """Can we reach Reddit RIGHT NOW, with no fixture fallback?

        `healthcheck` cannot answer this and never could: it goes through
        `_get`, so under the default live_then_fixture mode a total outage is
        absorbed by a replay and reported as healthy. That is how Reddit being
        100% blocked shows up as a PASS in test_environment while every user
        query returns "no threads found" -- the check was structurally
        incapable of seeing the thing it existed to detect.

        Returns (reachable, detail). Never raises.
        """
        try:
            resp = self._fetch_live("/r/Singapore/")
        except httpx.HTTPStatusError as exc:
            code = exc.response.status_code
            if code in (401, 403):
                return False, (
                    f"HTTP {code} — logged-out old.reddit access is blocked. "
                    "Only the recorded corpus can serve queries."
                )
            return False, f"HTTP {code}"
        except Exception as exc:  # noqa: BLE001
            return False, f"{type(exc).__name__}: {exc}"
        if 'data-fullname="t3_' not in resp.text:
            return False, "200 but no parseable posts — markup changed?"
        return True, "reachable"

    def healthcheck(self) -> bool:
        """Can the configured source serve a request at all — live OR replay.

        Deliberately NOT the same question as `probe_live`. This one answers
        "will the pipeline return data", which stays true on the fixture
        corpus; that is the honest status of a demo running on replay. Callers
        that need to know whether Reddit itself is up must ask probe_live, and
        the log line below says which one satisfied this check so a passing
        healthcheck can never quietly mean the wrong thing.
        """
        live_ok, detail = self.probe_live()
        if live_ok:
            return True
        logger.warning(
            "Reddit live access is down (%s) — healthcheck now reflects the "
            "recorded corpus only", detail
        )
        try:
            resp = self._get("/r/Singapore/")
            return 'data-fullname="t3_' in resp.text
        except Exception as exc:  # noqa: BLE001
            logger.error("Reddit healthcheck failed: %s", exc)
            return False

    # ---- Parsing helpers ----

    @staticmethod
    def _parse_points(text: str | None) -> int:
        """'1,234 points' -> 1234; anything unparseable -> 0."""
        if not text:
            return 0
        m = re.search(r"([\d,]+)\s*point", text)
        return int(m.group(1).replace(",", "")) if m else 0

    @staticmethod
    def _parse_count(text: str | None) -> int:
        """'86 comments' -> 86."""
        if not text:
            return 0
        m = re.search(r"([\d,]+)", text)
        return int(m.group(1).replace(",", "")) if m else 0

    # ---- Phase 1: data access ----

    def _parse_search_page(
        self, html: str, limit: int, subreddit: str | None = None
    ) -> list[RedditThread]:
        """Shared parser for subreddit-restricted and site-wide search pages.
        When `subreddit` is None it's read from each result's own link."""
        soup = BeautifulSoup(html, "lxml")
        threads: list[RedditThread] = []
        for el in soup.select("div.search-result-link")[:limit]:
            fullname = el.attrs.get("data-fullname", "")
            if not fullname.startswith("t3_"):
                continue
            title_a = el.select_one("a.search-title")
            time_el = el.select_one("time")
            author_a = el.select_one("a.author")
            if subreddit is None:
                sub_a = el.select_one("a.search-subreddit-link")
                sub = sub_a.get_text(strip=True).removeprefix("r/") if sub_a else ""
            else:
                sub = subreddit
            threads.append(
                RedditThread(
                    id=fullname.removeprefix("t3_"),
                    subreddit=sub,
                    author=author_a.get_text(strip=True) if author_a else "",
                    title=title_a.get_text(strip=True) if title_a else "",
                    url=(title_a.attrs.get("href", "") if title_a else ""),
                    score=self._parse_points(
                        el.select_one(".search-score").get_text()
                        if el.select_one(".search-score")
                        else None
                    ),
                    num_comments=self._parse_count(
                        el.select_one("a.search-comments").get_text()
                        if el.select_one("a.search-comments")
                        else None
                    ),
                    created_utc=_iso_to_utc(time_el.attrs.get("datetime")) if time_el else 0.0,
                    author_account_age_days=None,  # not available in HTML
                )
            )
        return threads

    def search_subreddit(
        self, subreddit: str, query: str, time_filter: str = "year", limit: int = 25
    ) -> list[RedditThread]:
        """Search one subreddit. Returns lightweight threads (no comments,
        search-page scores are unreliable) — call get_thread_with_comments on
        the ones worth reading."""
        try:
            resp = self._get(
                f"/r/{subreddit}/search/",
                params={
                    "q": query,
                    "restrict_sr": "on",
                    "sort": "relevance",
                    "t": time_filter,
                    "limit": str(limit),
                },
            )
        except httpx.HTTPStatusError as exc:
            # Nonexistent/banned/private subreddit → empty, not fatal.
            logger.warning("search r/%s -> HTTP %s", subreddit, exc.response.status_code)
            return []
        except FixtureMissing as exc:
            # Same outcome as above: this subreddit yields nothing for this
            # query. One uncaptured sub must not cost the whole brief.
            logger.warning("search r/%s -> %s", subreddit, exc)
            return []

        threads = self._parse_search_page(resp.text, limit, subreddit=subreddit)
        logger.info("search r/%s %r -> %d results", subreddit, query, len(threads))
        return threads

    def get_thread_with_comments(
        self, thread_id: str, max_comments: int = 15
    ) -> RedditThread:
        """Fetch full thread: reliable score, selftext, top-level comments
        sorted by top. One request."""
        resp = self._get(f"/comments/{thread_id}/", params={"limit": "100", "sort": "top"})
        soup = BeautifulSoup(resp.text, "lxml")

        post = soup.select_one("div.thing.link")
        if post is None:
            raise ValueError(f"thread {thread_id}: no post element in HTML")

        title_a = post.select_one("a.title")
        selftext_el = soup.select_one("div.expando div.usertext-body")

        comments: list[str] = []
        for cm in soup.select("div.commentarea > div.sitetable > div.thing.comment"):
            author = cm.attrs.get("data-author", "")
            if author in ("AutoModerator", "[deleted]"):
                continue
            body_el = cm.select_one("div.usertext-body")
            if body_el is None:
                continue
            body = body_el.get_text(" ", strip=True)
            if not body:
                continue
            # Recent threads hide comment scores; render those as unknown
            # rather than "[0 pts]" so the synthesis prompt's upvote weighting
            # doesn't systematically discount fresh comments.
            score_el = cm.select_one("span.score.unvoted")
            if score_el is None:
                comments.append(f"[score hidden] {body}")
            else:
                points = self._parse_points(score_el.get_text())
                comments.append(f"[{points} pts] {body}")
            if len(comments) >= max_comments:
                break

        ts_ms = post.attrs.get("data-timestamp")
        return RedditThread(
            id=thread_id,
            subreddit=post.attrs.get("data-subreddit", ""),
            author=post.attrs.get("data-author", ""),
            title=title_a.get_text(strip=True) if title_a else "",
            body=selftext_el.get_text(" ", strip=True) if selftext_el else "",
            url=f"https://old.reddit.com/comments/{thread_id}/",
            score=int(post.attrs.get("data-score", 0) or 0),
            num_comments=int(post.attrs.get("data-comments-count", 0) or 0),
            created_utc=(int(ts_ms) / 1000.0) if ts_ms else 0.0,
            author_account_age_days=None,
            top_comments=comments,
        )

    def apply_signal_filters(
        self,
        threads: list[RedditThread],
        min_score: int = 10,
        min_comments: int = 3,
    ) -> list[RedditThread]:
        """Signal hygiene (see docs/SIGNAL_FILTERS.md).

        HTML-era version: upvote + engagement thresholds, dedupe. Thresholds
        relax rather than return nothing — a thin result set with honest
        labelling beats an empty one. Account-age filtering isn't possible
        without per-author requests; the synthesis prompt carries the
        skepticism instructions instead.
        """
        seen: set[str] = set()
        unique = [t for t in threads if not (t.id in seen or seen.add(t.id))]

        strong = [
            t for t in unique if t.score >= min_score and t.num_comments >= min_comments
        ]
        if len(strong) >= 5:
            result = strong
        else:
            # Relax: keep anything with real engagement, sorted best-first.
            result = [t for t in unique if t.score > 0 or t.num_comments > 0] or unique
            logger.info(
                "signal filters relaxed: %d strong of %d unique", len(strong), len(unique)
            )
        return sorted(result, key=lambda t: (t.score, t.num_comments), reverse=True)

    # ---- Phase 2 ----

    # Phrasings people actually use in retrospective/outcome post titles.
    # Kept short: each template × subreddit is one paced HTTP request.
    RETROSPECTIVE_TEMPLATES = (
        "{topic} update",
        "{topic} months later",
        "{topic} regret",
        "{topic} worth it",
    )

    def search_retrospective(
        self,
        topic: str,
        subreddits: list[str],
        per_search_limit: int = 10,
        extra_topics: list[str] | None = None,
    ) -> list[RedditThread]:
        """Hunt update/outcome posts about a decision across subreddits.

        Runs each retrospective query template against each subreddit for the
        primary topic phrasing, plus site-wide passes for the primary and any
        extra phrasings, then dedupes. Results are CANDIDATES — titles
        matching retrospective phrasing — and still need LLM classification
        (a title like "X worth it?" is usually a prospective question, not an
        outcome report).
        """
        candidates: list[RedditThread] = []
        for sub in subreddits:
            for template in self.RETROSPECTIVE_TEMPLATES:
                candidates.extend(
                    self.search_subreddit(
                        sub,
                        template.format(topic=topic),
                        time_filter="all",
                        limit=per_search_limit,
                    )
                )
        # Site-wide passes: update posts often live outside the obvious subs
        # (r/self, r/TrueOffMyChest, niche subs the LLM didn't name). Extra
        # topic phrasings counter Reddit search's literal keyword matching.
        for t in [topic, *(extra_topics or [])]:
            candidates.extend(self._search_all(f"{t} update", limit=per_search_limit))

        seen: set[str] = set()
        unique = [t for t in candidates if not (t.id in seen or seen.add(t.id))]
        logger.info(
            "retrospective search %r: %d candidates (%d unique)",
            topic, len(candidates), len(unique),
        )
        return unique

    # ---- Phase 3: monitoring ----

    def scan_recent(
        self, subreddits: list[str], query: str, limit_per_sub: int = 10
    ) -> list[RedditThread]:
        """Recent mentions of a monitored item: past-week posts sorted newest
        first, one search per subreddit. Search-page scores are unreliable but
        fine here — the worker's LLM classifier judges content, and the alert
        engine compares run-over-run counts, not absolute scores."""
        found: list[RedditThread] = []
        for sub in subreddits:
            try:
                resp = self._get(
                    f"/r/{sub}/search/",
                    params={
                        "q": query,
                        "restrict_sr": "on",
                        "sort": "new",
                        "t": "week",
                        "limit": str(limit_per_sub),
                    },
                )
            except httpx.HTTPStatusError as exc:
                logger.warning("scan r/%s -> HTTP %s", sub, exc.response.status_code)
                continue
            except FixtureMissing as exc:
                logger.warning("scan r/%s -> %s", sub, exc)
                continue
            found.extend(self._parse_search_page(resp.text, limit_per_sub, subreddit=sub))
        seen: set[str] = set()
        return [t for t in found if not (t.id in seen or seen.add(t.id))]

    def _search_all(self, query: str, limit: int = 10) -> list[RedditThread]:
        """Site-wide old.reddit search (no subreddit restriction)."""
        try:
            resp = self._get(
                "/search/",
                params={"q": query, "sort": "relevance", "t": "all", "limit": str(limit)},
            )
        except httpx.HTTPStatusError as exc:
            logger.warning("site-wide search -> HTTP %s", exc.response.status_code)
            return []
        except FixtureMissing as exc:
            logger.warning("site-wide search -> %s", exc)
            return []
        return self._parse_search_page(resp.text, limit, subreddit=None)


def _iso_to_utc(iso: str | None) -> float:
    """'2026-01-22T16:28:24+00:00' -> unix timestamp; bad input -> 0."""
    if not iso:
        return 0.0
    from datetime import datetime

    try:
        return datetime.fromisoformat(iso).timestamp()
    except ValueError:
        return 0.0
