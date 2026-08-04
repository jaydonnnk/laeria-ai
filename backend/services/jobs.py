"""Background jobs for work that outlives an HTTP request.

Mode 1 takes 2-4 minutes inside the request handler. Nothing between the
browser and the app tolerates that: Cloudflare cuts at 100s and most PaaS
ingress somewhere between 30 and 120. The request 504s while the server keeps
burning paid LLM calls into a socket nobody is listening to, and the user sees
a generic error for work that actually succeeded.

So long work is submitted, returns a job id immediately, and is polled.

In-process and in-memory, deliberately: this is a single-instance deployment,
jobs live for minutes, and a durable queue would be a second system to operate
for no benefit at this size. A restart loses running jobs — acceptable, and
stated rather than hidden. If the backend is ever scaled past one instance,
this must move to a shared store, because a poll could otherwise land on an
instance that never ran the job.
"""

from __future__ import annotations

import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

from core.logging import get_logger

logger = get_logger(__name__)

# Long research jobs are mostly waiting on Reddit and the LLM, so a small pool
# is plenty — and the Reddit pacing lock serialises the slow part anyway.
_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="job")
_jobs: dict[str, dict[str, Any]] = {}
_lock = threading.Lock()

# Finished jobs are kept long enough for a client to collect the result, then
# dropped so the dict cannot grow without bound.
_RETAIN_SECONDS = 30 * 60


def _prune() -> None:
    cutoff = time.time() - _RETAIN_SECONDS
    for jid, job in list(_jobs.items()):
        if job["status"] in ("done", "error") and job.get("finished_at", 0) < cutoff:
            _jobs.pop(jid, None)


def submit(fn: Callable[..., Any], *args: Any, label: str = "", **kwargs: Any) -> str:
    """Start work in the background and return its job id."""
    job_id = uuid.uuid4().hex[:16]
    with _lock:
        _prune()
        _jobs[job_id] = {
            "status": "pending",
            "label": label,
            "submitted_at": time.time(),
            "started_at": None,
            "finished_at": None,
            "result": None,
            "error": None,
        }

    def run() -> None:
        with _lock:
            job = _jobs.get(job_id)
            if job is None:  # pruned before it started; nothing to report to
                return
            job["status"] = "running"
            job["started_at"] = time.time()
        try:
            value = fn(*args, **kwargs)
            with _lock:
                if job_id in _jobs:
                    _jobs[job_id].update(
                        status="done", result=value, finished_at=time.time()
                    )
        except Exception as exc:  # noqa: BLE001
            logger.exception("job %s (%s) failed", job_id, label)
            with _lock:
                if job_id in _jobs:
                    _jobs[job_id].update(
                        status="error", error=str(exc), finished_at=time.time()
                    )

    _pool.submit(run)
    logger.info("job %s submitted (%s)", job_id, label or fn.__name__)
    return job_id


def get(job_id: str) -> dict[str, Any] | None:
    """Current state of a job, with elapsed time so a client can show progress."""
    with _lock:
        job = _jobs.get(job_id)
        if job is None:
            return None
        snapshot = dict(job)

    started = snapshot.get("started_at") or snapshot["submitted_at"]
    end = snapshot.get("finished_at") or time.time()
    snapshot["elapsed_seconds"] = round(end - started, 1)
    return snapshot
