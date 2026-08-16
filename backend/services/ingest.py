"""Discussions the user handed us from their own browser.

Reddit will not serve this backend, but it serves the user perfectly well. So
when someone opens a discussion they want weighed, the extension offers to send
that page — and only that page, and only after they click — to Laeria.

WHAT THIS IS NOT. It is not a crawler. Nothing here opens a Reddit page,
follows a link, logs in, or reuses a session. The content arrives because a
human read it and chose to share it, which is the same act as pasting the text
into the box, with fewer keystrokes.

TRUST. Being user-supplied makes the transport legitimate; it makes the CONTENT
no safer at all. A Reddit comment is written by a stranger whether the server
fetched it or the browser did, so an ingested thread is untrusted external text
and goes through exactly the same Bedrock input boundary as everything else —
in `ResearchAgent._guard_threads`, unchanged. Nothing here screens content,
because screening it here would put a second, divergent boundary in the code.

Numbers the browser reports (score, comment count) are DISPLAY DATA. They are
bounded and sanity-checked, never used for a security decision, and never
believed enough to authorise anything.

In memory, with a TTL, deliberately: the content is somebody's browsing, it is
only needed between "send this" and "analyse", and writing it to disk would
turn a transient convenience into a data-retention question nobody asked for.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass

from core.logging import get_logger
from core.models import RedditThread

logger = get_logger(__name__)

# Bounds. A Reddit thread is prose; anything past these is either a mistake or
# an attempt to make the model read a novel, and both are refused the same way.
MAX_TITLE = 400
MAX_BODY = 20_000
MAX_COMMENTS = 40
MAX_COMMENT = 4_000
MAX_PAYLOAD = 250_000        # total characters across the whole submission
MAX_REPORTED_COUNT = 10_000_000


@dataclass(frozen=True)
class Captured:
    thread: RedditThread
    received_at: float


_store: dict[tuple[str, str], Captured] = {}
_lock = threading.Lock()


class IngestTooLarge(ValueError):
    """The submission exceeds a documented bound. Named so the route can answer
    413 rather than turning a size problem into a generic 500."""


def _clip(value: object, limit: int) -> str:
    return str(value or "")[:limit]


def _int(value: object, limit: int = MAX_REPORTED_COUNT) -> int:
    """A browser-reported number, clamped into sanity. Never trusted for
    anything but display and tie-breaking."""
    try:
        n = int(value)
    except (TypeError, ValueError):
        return 0
    return max(0, min(n, limit))


def build_thread(payload: dict, thread_id: str, subreddit: str) -> RedditThread:
    """A captured page as the model the rest of the pipeline speaks.

    Raises IngestTooLarge when the submission is past its bounds. The id and
    subreddit come from the URL the route already validated, not from the
    payload — so a page cannot claim to be a different thread than the one its
    URL names.
    """
    total = len(str(payload.get("body") or "")) + sum(
        len(str(c.get("body") or "")) for c in (payload.get("comments") or [])[:200]
    )
    if total > MAX_PAYLOAD:
        raise IngestTooLarge(
            f"submission is {total} characters, over the {MAX_PAYLOAD} limit"
        )

    comments: list[str] = []
    for raw in (payload.get("comments") or [])[:MAX_COMMENTS]:
        if not isinstance(raw, dict):
            continue
        body = _clip(raw.get("body"), MAX_COMMENT).strip()
        if not body:
            continue
        # Rendered exactly like the scraped path so `_build_corpus` and the
        # synthesis prompt's upvote weighting see one consistent shape.
        score = raw.get("score")
        prefix = f"[{_int(score)} pts]" if score is not None else "[score hidden]"
        comments.append(f"{prefix} {body}")

    return RedditThread(
        id=thread_id,
        subreddit=subreddit or _clip(payload.get("subreddit"), 64),
        title=_clip(payload.get("title"), MAX_TITLE).strip(),
        body=_clip(payload.get("body"), MAX_BODY).strip(),
        url=f"https://www.reddit.com/comments/{thread_id}/",
        score=_int(payload.get("score")),
        num_comments=_int(payload.get("num_comments")),
        created_utc=0.0,
        author=_clip(payload.get("author"), 64),
        top_comments=comments,
    )


def put(user_id: str, thread: RedditThread) -> None:
    with _lock:
        _prune()
        _store[(user_id, thread.id)] = Captured(thread=thread, received_at=time.time())
    # The thread id only. Never the title, body or comments — that is somebody's
    # browsing, and a log is the wrong place for it.
    logger.info("ingested browser-supplied thread %s (%d comments)",
                thread.id, len(thread.top_comments))


def get(user_id: str, thread_id: str) -> RedditThread | None:
    with _lock:
        _prune()
        found = _store.get((user_id, thread_id))
    return found.thread if found else None


def _prune() -> None:
    from core.config import get_settings

    ttl = get_settings().ingest_ttl_seconds
    cutoff = time.time() - ttl
    for key, item in list(_store.items()):
        # `<=`, not `<`: a TTL of 0 must mean "keep nothing", and on a coarse
        # clock (Windows ticks at ~15ms) a strict comparison lets an entry
        # written in the same tick as the cutoff survive forever.
        if item.received_at <= cutoff:
            _store.pop(key, None)


def clear() -> None:
    """Test hook. Never called from a request path."""
    with _lock:
        _store.clear()
