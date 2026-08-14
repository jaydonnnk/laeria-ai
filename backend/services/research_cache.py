"""Disk cache for completed research briefs.

A Mode 2 query costs ~20 paced Reddit requests and two LLM calls — about a
minute of wall time and real money — to answer a question whose answer does not
change hour to hour. Threads about a two-year-old product are the same threads
tomorrow.

On disk rather than in memory so it survives a restart: the case that matters
most is a rehearsal or a demo where the same query is run repeatedly, and an
in-memory cache is empty exactly when the process was just restarted.

Writes are atomic (temp file + replace) so a cache read can never see a
half-written brief, and a corrupt entry is treated as a miss rather than an
error — a cache must never be able to break the thing it is accelerating.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from pathlib import Path

from core.logging import get_logger

logger = get_logger(__name__)

CACHE_DIR = Path(__file__).resolve().parent.parent / ".cache" / "research"

# Cache kind for Mode 2 briefs, versioned because the SHAPE of a brief changed.
#
# Entries written before the structural confidence policy carry a verdict the
# LLM authored alone, with no ceiling applied and no reasons attached. Served
# from cache they would bypass the policy entirely and present themselves as
# calibrated — a stale HIGH that no structural rule ever got to examine, for a
# full TTL after the upgrade shipped.
#
# The kind is part of the cache key, so bumping it makes every pre-upgrade
# entry a miss and the query recomputes through the new pipeline. Old files are
# left alone rather than deleted: they simply become unreachable, which is the
# cheapest correct migration and keeps the change reversible.
#
# Bump this whenever the meaning of a stored brief changes again.
DECISION_CACHE_KIND = "decision-v2"


def _key(query: str, context: str, kind: str) -> str:
    """Normalised so trivial rewording still hits: case, surrounding
    whitespace, and internal run-length are not meaningful to the answer."""
    norm = re.sub(r"\s+", " ", f"{kind}|{query}|{context}".strip().lower())
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()[:24]


def get(query: str, context: str = "", kind: str = "decision", ttl_seconds: int = 86_400) -> dict | None:
    if ttl_seconds <= 0:
        return None
    f = CACHE_DIR / f"{_key(query, context, kind)}.json"
    if not f.exists():
        return None
    try:
        payload = json.loads(f.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        logger.warning("unreadable cache entry %s — treating as a miss", f.name)
        return None

    age = time.time() - float(payload.get("cached_at", 0))
    if age > ttl_seconds:
        return None
    logger.info("research cache hit for %r (age %.0fs)", query[:60], age)
    return payload.get("brief")


def put(query: str, brief: dict, context: str = "", kind: str = "decision") -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    f = CACHE_DIR / f"{_key(query, context, kind)}.json"
    tmp = f.with_suffix(".tmp")
    try:
        tmp.write_text(
            json.dumps({"query": query, "cached_at": time.time(), "brief": brief}),
            encoding="utf-8",
        )
        os.replace(tmp, f)  # atomic — a reader never sees a partial file
    except OSError as exc:
        logger.warning("could not write research cache: %s", exc)
        tmp.unlink(missing_ok=True)


def clear() -> int:
    if not CACHE_DIR.exists():
        return 0
    n = 0
    for f in CACHE_DIR.glob("*.json"):
        f.unlink()
        n += 1
    return n
