"""Usage tracking (Phase 5) — how much the agent actually consumes.

File-backed counters (backend/usage_stats.json): fine for a personal
single-machine deployment, survives restarts, and works across the API
process and the worker process (last-writer wins per increment via
read-modify-write under a lock file; contention is negligible at our rates).

Tracked:
    reddit_requests   — every old.reddit HTTP request
    llm_calls         — chat completions
    llm_tokens        — total tokens (from provider usage blocks)
    embed_calls       — embedding batches
    paid_usd          — executed payment volume
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

_PATH = Path(__file__).resolve().parent.parent / "usage_stats.json"
_LOCK = threading.Lock()

_DEFAULT = {
    "reddit_requests": 0,
    "llm_calls": 0,
    "llm_tokens": 0,
    "embed_calls": 0,
    "paid_usd": 0.0,
}


def _read() -> dict:
    try:
        return {**_DEFAULT, **json.loads(_PATH.read_text(encoding="utf-8"))}
    except Exception:  # noqa: BLE001
        return dict(_DEFAULT)


def incr(key: str, amount: float = 1) -> None:
    """Best-effort increment; tracking must never break a feature."""
    try:
        with _LOCK:
            stats = _read()
            stats[key] = round(stats.get(key, 0) + amount, 6)
            _PATH.write_text(json.dumps(stats), encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass


def snapshot() -> dict:
    return _read()
