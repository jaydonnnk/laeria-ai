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
    # Identified by its own filename — a hash — never by the query.
    #
    # The caller looks entries up by the words the user actually typed, which
    # is the right identity for a cache but the wrong thing to write to a log:
    # if the safety layer masked an address out of that question, reproducing
    # it here would put it straight back. The hash is stable, greppable, and
    # not user-controlled.
    logger.info("research cache hit %s (age %.0fs)", f.name, age)
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
