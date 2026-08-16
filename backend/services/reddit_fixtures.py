"""Record/replay store for Reddit HTML.

Why this exists: on 2026-06-30 Reddit announced that old.reddit.com will
require a login, rolling out "over the next month", explicitly to stop
logged-out automated traffic. Our only data source is logged-out
old.reddit.com HTML. That rollout window contains demo day.

So the demo cannot depend on a live fetch succeeding. This captures the raw
HTML for a known set of queries once, while logged-out access still works, and
replays it byte-for-byte afterwards. Because it intercepts at the HTTP layer,
every parser and agent above it runs the real code path — the only thing
swapped is where the bytes came from.

This is not a workaround for the underlying problem (see
docs/REMEDIATION_PLAN.md §A1b — the Reddit Data API application is the actual
answer). It buys a demo that cannot 403 on stage, and a deterministic corpus
to develop against without hammering anyone's servers.

Modes (REDDIT_SOURCE):
  live               fetch only — no cache read, no cache write
  record             fetch and persist every response as a fixture
  fixture            replay only — a miss is an error, never a silent fetch
  live_then_fixture  try live; on any failure fall back to a fixture (default
                     for demos: real when it works, deterministic when it
                     doesn't)
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode

from core.logging import get_logger

logger = get_logger(__name__)

FIXTURE_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "reddit"
_INDEX = FIXTURE_DIR / "index.json"

VALID_MODES = {"live", "record", "fixture", "live_then_fixture"}


def key_for(path: str, params: dict | None) -> str:
    """Stable key for a request. Params are sorted so dict ordering can't
    produce two fixtures for the same logical fetch."""
    qs = urlencode(sorted((params or {}).items()))
    raw = f"{path}?{qs}" if qs else path
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]


def _descriptor(path: str, params: dict | None) -> str:
    qs = urlencode(sorted((params or {}).items()))
    return f"{path}?{qs}" if qs else path


def load(path: str, params: dict | None) -> str | None:
    """Replay the recorded body, or None if we never captured this request."""
    f = FIXTURE_DIR / f"{key_for(path, params)}.html"
    if not f.exists():
        return None
    return f.read_text(encoding="utf-8")


def load_error(path: str, params: dict | None) -> int | None:
    """Recorded HTTP failure status for this request, if it failed at capture.

    A run that contained a handled 403 (the LLM proposing a subreddit that
    does not exist, say) must replay as that same 403 — otherwise the corpus
    cannot reproduce the run it was captured from, and replay diverges from
    the behaviour the demo was rehearsed against.
    """
    f = FIXTURE_DIR / f"{key_for(path, params)}.error.json"
    if not f.exists():
        return None
    try:
        return int(json.loads(f.read_text(encoding="utf-8"))["status"])
    except (json.JSONDecodeError, KeyError, ValueError):
        return None


def _index_put(key: str, entry: dict) -> None:
    index = {}
    if _INDEX.exists():
        try:
            index = json.loads(_INDEX.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            logger.warning("fixture index unreadable — rebuilding")
    index[key] = {**entry, "captured_at": datetime.now(timezone.utc).isoformat()}
    _INDEX.write_text(json.dumps(index, indent=2, sort_keys=True), encoding="utf-8")


def save(path: str, params: dict | None, body: str) -> None:
    """Persist a response body and note it in the human-readable index."""
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    key = key_for(path, params)
    (FIXTURE_DIR / f"{key}.html").write_text(body, encoding="utf-8")
    _index_put(key, {"request": _descriptor(path, params), "bytes": len(body)})


def save_error(path: str, params: dict | None, status: int) -> None:
    """Persist a failed request so replay reproduces the failure."""
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    key = key_for(path, params)
    (FIXTURE_DIR / f"{key}.error.json").write_text(
        json.dumps({"request": _descriptor(path, params), "status": status}, indent=2),
        encoding="utf-8",
    )
    _index_put(key, {"request": _descriptor(path, params), "error_status": status})


# ---- research plans -------------------------------------------------------
#
# The subreddit/query plan is an LLM call, and its output decides which URLs
# get fetched. Replaying HTML without replaying the plan is useless: a fresh
# plan asks for pages that were never captured, so every fixture misses. The
# plan is therefore part of the corpus.

_PLAN_DIR = FIXTURE_DIR / "plans"


def load_plan(query: str) -> dict | None:
    f = _PLAN_DIR / f"{key_for(query, None)}.json"
    if not f.exists():
        return None
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        logger.warning("unreadable plan fixture for %r", query)
        return None


def save_plan(query: str, plan: dict) -> None:
    _PLAN_DIR.mkdir(parents=True, exist_ok=True)
    (_PLAN_DIR / f"{key_for(query, None)}.json").write_text(
        json.dumps({"query": query, **plan}, indent=2), encoding="utf-8"
    )


def captured_at() -> str | None:
    """Oldest capture timestamp in the corpus — what the UI should cite when
    it says results came from a frozen snapshot."""
    if not _INDEX.exists():
        return None
    try:
        index = json.loads(_INDEX.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    stamps = [v.get("captured_at") for v in index.values() if v.get("captured_at")]
    return min(stamps) if stamps else None


def count() -> int:
    if not FIXTURE_DIR.exists():
        return 0
    return len(list(FIXTURE_DIR.glob("*.html")))
