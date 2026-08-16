"""Finding current community discussions without touching Reddit.

Reddit refuses automated access from hosted infrastructure, and Laeria does not
try to get around that: no proxies, no IP rotation, no automated logins, no
cookie reuse. So discovery asks a WEB SEARCH provider which Reddit discussions
exist for a question, and the backend never sends a request to reddit.com.

What comes back is a URL, a title and a search snippet — real, publicly indexed
text, but a preview rather than a conversation. Turning a preview into a full
discussion is the user's call, made in their own browser (see the extension and
`/research/ingest`). This module's only job is to find the candidates and hand
them to the existing ranking.

WHY A PROTOCOL AND NOT A FRAMEWORK. `ResearchAgent` already talks to its data
source through a handful of methods, which is the seam a future
`RedditOAuthSource` slots into unchanged. `DiscoverySource` below is that seam
written down — three methods, no registry, no plugin loader.

PROVENANCE IS CARRIED SEPARATELY. A discovered candidate becomes an ordinary
`RedditThread` so every downstream stage — guardrails, relevance, signal
analysis, synthesis — runs unmodified. How much of the thread we actually hold
travels alongside as an `Acquisition`, keyed by thread id, the same way
`_guard_threads` already returns sanitized text in a side map. Nothing about
confidence changes; the pipeline is simply told what it is looking at.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol
from urllib.parse import urlparse

from core.logging import get_logger
from core.models import RedditThread

logger = get_logger(__name__)


class Acquisition(str, Enum):
    """How completely we hold a discussion. Displayed, never used to weight
    evidence — the synthesis prompt reasons about the text it is given, and a
    thinner source produces a thinner answer on its own."""

    FULL_BROWSER = "full_browser"      # the user sent us the page they opened
    RECORDED_FULL = "recorded_full"    # a full thread captured in the corpus
    SEARCH_PREVIEW = "search_preview"  # title + snippet from web search only
    REDDIT_OAUTH = "reddit_oauth"      # reserved: official API, once approved


# Human labels for the UI. Kept beside the enum so a new level cannot be added
# without deciding what a user should be told it means.
ACQUISITION_LABELS = {
    Acquisition.FULL_BROWSER: "Full discussion",
    Acquisition.RECORDED_FULL: "Recorded discussion",
    Acquisition.SEARCH_PREVIEW: "Search preview",
    Acquisition.REDDIT_OAUTH: "Full discussion",
}


@dataclass(frozen=True)
class Candidate:
    """One discussion a search provider says exists."""

    thread_id: str
    url: str
    title: str
    snippet: str = ""
    subreddit: str = ""
    published: str = ""
    provider: str = ""


class DiscoverySource(Protocol):
    """The seam. A future RedditOAuthSource implements exactly this."""

    name: str

    def search(self, query: str, limit: int = 12) -> list[Candidate]: ...

    def available(self) -> tuple[bool, str]: ...


# ---- Reddit URL handling -------------------------------------------------
#
# Search providers return whatever they indexed, so every URL is checked here
# rather than trusted: a non-Reddit result must never enter a "community
# evidence" list, and a thread id is what the corpus and the extension are both
# keyed by.

_REDDIT_HOSTS = {
    "reddit.com", "www.reddit.com", "old.reddit.com",
    "new.reddit.com", "np.reddit.com", "m.reddit.com",
}
# /r/<sub>/comments/<id>/<slug>  and the short /comments/<id> form.
_THREAD_RE = re.compile(
    r"^/(?:r/(?P<sub>[A-Za-z0-9_]{2,21})/)?comments/(?P<id>[a-z0-9]{4,12})(?:/|$)"
)


def parse_reddit_url(url: str) -> tuple[str, str] | None:
    """(thread_id, subreddit) for a Reddit COMMENTS url, else None.

    Rejects non-Reddit hosts, Reddit pages that are not threads (subreddit
    fronts, user pages, wikis), and anything unparseable. Returning None is the
    whole point: it is what stops a search provider's stray result being
    presented as a community discussion.
    """
    if not url or len(url) > 2048:
        return None
    try:
        parts = urlparse(url if "//" in url else f"https://{url}")
    except ValueError:
        return None
    if parts.scheme not in ("http", "https"):
        return None
    if (parts.hostname or "").lower() not in _REDDIT_HOSTS:
        return None
    m = _THREAD_RE.match(parts.path or "")
    if not m:
        return None
    return m.group("id"), (m.group("sub") or "")


def canonical_url(thread_id: str) -> str:
    return f"https://www.reddit.com/comments/{thread_id}/"


def to_thread(c: Candidate) -> RedditThread:
    """A discovered candidate as the model the rest of the pipeline speaks.

    `body` carries the search snippet, which is honest — it is the only body
    text we have — and it is what lets relevance ranking read more than a
    title. Score and comment count are left at zero because the provider does
    not know them; `apply_signal_filters` relaxes rather than discarding when
    engagement is unknown, so a preview is ranked on what it says instead of on
    numbers nobody supplied.
    """
    return RedditThread(
        id=c.thread_id,
        subreddit=c.subreddit,
        title=c.title,
        body=c.snippet,
        url=c.url or canonical_url(c.thread_id),
        score=0,
        num_comments=0,
        created_utc=0.0,
        author="",
        top_comments=[],
    )


def dedupe(candidates: list[Candidate]) -> list[Candidate]:
    """First mention of each thread id wins — providers routinely return the
    same discussion under old.reddit, www and a slugged variant."""
    seen: set[str] = set()
    out: list[Candidate] = []
    for c in candidates:
        if c.thread_id and c.thread_id not in seen:
            seen.add(c.thread_id)
            out.append(c)
    return out


# ---- providers -----------------------------------------------------------
#
# Two, because the free tiers differ and whichever key exists should work. Each
# is a response-shape adapter and nothing more; product logic lives above.


@dataclass
class _HTTPProvider:
    api_key: str
    base_url: str
    name: str = "http"
    timeout: float = 12.0
    _extra: dict = field(default_factory=dict)

    def available(self) -> tuple[bool, str]:
        if not self.api_key:
            return False, f"{self.name}: no API key configured"
        return True, f"{self.name}: configured"


class BraveDiscovery(_HTTPProvider):
    """Brave Search API. Free tier is ample for a demo."""

    name = "brave"

    def search(self, query: str, limit: int = 12) -> list[Candidate]:
        import httpx

        resp = httpx.get(
            self.base_url or "https://api.search.brave.com/res/v1/web/search",
            params={"q": f"site:reddit.com {query}", "count": min(limit, 20)},
            headers={"Accept": "application/json", "X-Subscription-Token": self.api_key},
            timeout=self.timeout,
        )
        resp.raise_for_status()
        return _from_rows(
            resp.json().get("web", {}).get("results", []),
            url_key="url", title_key="title", snippet_key="description",
            date_key="age", provider=self.name,
        )


class TavilyDiscovery(_HTTPProvider):
    """Tavily. Returns longer snippets, which is worth real evidence quality
    when a preview may be all we get."""

    name = "tavily"

    def search(self, query: str, limit: int = 12) -> list[Candidate]:
        import httpx

        resp = httpx.post(
            self.base_url or "https://api.tavily.com/search",
            json={
                "api_key": self.api_key,
                "query": f"site:reddit.com {query}",
                "max_results": min(limit, 20),
                "include_domains": ["reddit.com"],
            },
            timeout=self.timeout,
        )
        resp.raise_for_status()
        return _from_rows(
            resp.json().get("results", []),
            url_key="url", title_key="title", snippet_key="content",
            date_key="published_date", provider=self.name,
        )


class NullDiscovery:
    """No provider configured. Says so plainly instead of pretending to search.

    This is the default, and it is why a test run can never spend a real search
    quota: with no key there is nothing to call.
    """

    name = "none"

    def search(self, query: str, limit: int = 12) -> list[Candidate]:
        return []

    def available(self) -> tuple[bool, str]:
        return False, (
            "no discovery provider configured — set DISCOVERY_PROVIDER and "
            "DISCOVERY_API_KEY to search for current discussions"
        )


def _from_rows(rows, *, url_key, title_key, snippet_key, date_key, provider):
    """Provider rows -> Candidates, dropping anything that is not a Reddit
    thread. Every provider funnels through here so the URL rule is enforced in
    one place rather than per-adapter."""
    out: list[Candidate] = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        parsed = parse_reddit_url(str(row.get(url_key) or ""))
        if parsed is None:
            continue
        thread_id, sub = parsed
        out.append(
            Candidate(
                thread_id=thread_id,
                url=canonical_url(thread_id),
                title=str(row.get(title_key) or "").strip()[:300],
                snippet=str(row.get(snippet_key) or "").strip()[:1200],
                subreddit=sub,
                published=str(row.get(date_key) or "")[:40],
                provider=provider,
            )
        )
    return dedupe(out)


def get_source() -> DiscoverySource:
    """The configured provider, or a Null one that admits it cannot search."""
    from core.config import get_settings

    s = get_settings()
    provider = (s.discovery_provider or "none").strip().lower()
    if provider in ("", "none", "off"):
        return NullDiscovery()
    if provider == "brave":
        return BraveDiscovery(api_key=s.discovery_api_key, base_url=s.discovery_base_url)
    if provider == "tavily":
        return TavilyDiscovery(api_key=s.discovery_api_key, base_url=s.discovery_base_url)
    logger.warning("unknown DISCOVERY_PROVIDER %r — discovery disabled", provider)
    return NullDiscovery()
