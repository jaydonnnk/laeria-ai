"""Monitor worker — long-running background job for Mode 3.

Deployment target is the Hetzner VPS as a systemd service (see
infra/systemd/laeria-monitor.service); it also runs fine locally with
`python -m workers.monitor_worker`.

Wakes every POLL_SLEEP_SECONDS, checks only the items whose interval has
elapsed. check_item() is the single-item pipeline and is also called
synchronously by the API's run-now endpoint, so worker and dashboard share
one code path:

    scan recent posts (HTML search) -> classify run (LLM) -> persist run
    -> alert engine (change vs baseline) -> persist alert
"""

from __future__ import annotations

import time

from core.logging import configure_logging, get_logger
from core.models import SignalLevel

configure_logging()
logger = get_logger(__name__)

POLL_SLEEP_SECONDS = 60 * 30  # wake every 30 min, act only on due items


def check_item(item_row: dict, reddit=None, engine=None) -> dict:
    """Run one monitoring check for one item. Returns a summary dict
    {run, alert} — alert is None when nothing warrants attention."""
    from agents.alert_engine import AlertEngine
    from db import repositories as repo
    from services.reddit import RedditService

    reddit = reddit or RedditService()
    engine = engine or AlertEngine()

    item_id = item_row["id"]
    name = item_row["name"]
    subreddits = item_row.get("subreddits") or []
    if not subreddits:
        logger.warning("item %s (%s) has no subreddits; skipping", item_id, name)
        repo.touch_item_checked(item_id)
        return {"run": None, "alert": None}

    # History BEFORE this run — the baseline the alert engine compares against.
    history = repo.recent_runs(item_id, limit=10)

    posts = reddit.scan_recent(subreddits, name)
    findings = engine.classify_run(name, posts)
    run_row = repo.create_run(
        item_id=item_id,
        posts_found=len(posts),
        sentiment=findings["sentiment"],
        signal_level=SignalLevel(findings["signal_level"]),
        raw_findings=findings,
    )
    repo.touch_item_checked(item_id)

    alert = engine.evaluate(item_id, run_row["id"], findings, history)
    alert_row = None
    if alert is not None:
        alert_row = repo.create_alert(alert)
        logger.info("ALERT [%s] %s: %s", alert.severity.value, name, alert.summary)
        _notify(name, alert.severity.value, alert.summary)
        _propose_alert_action(alert, alert_row, name)

    logger.info(
        "checked %s: %d posts, signal=%s%s",
        name, len(posts), findings["signal_level"],
        " -> ALERT" if alert_row else "",
    )
    return {"run": run_row, "alert": alert_row}


def _propose_alert_action(alert, alert_row: dict, item_name: str) -> None:
    """Mode 3 trigger: an alert carrying a recommended action becomes a
    PENDING action for the user to approve (24h window). Monitoring never
    executes autonomously — the human approves, then the action agent runs.
    Best-effort: an action-proposal failure must not kill the check."""
    from datetime import datetime, timedelta, timezone

    from core.config import get_settings
    from core.models import ActionType
    from db import repositories as repo

    if alert.recommended_action == ActionType.NONE:
        return
    try:
        expires_at = (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat()
        # Replacements are payable (x402 vendor); cancellations have no API —
        # approval executes the manual-cancel flow (Obsidian note + log).
        target = (
            get_settings().action_vendor_url
            if alert.recommended_action == ActionType.ORDER_REPLACEMENT
            else item_name
        )
        repo.create_action(
            alert.recommended_action.value,
            target,
            "pending_approval",
            0.0,
            {
                "description": f"[monitor] {item_name}: {alert.summary[:160]}",
                "reason": f"recommended by alert ({alert.severity.value})",
                "alert_id": alert_row.get("id"),
                "expires_at": expires_at,
            },
        )
        logger.info("proposed %s action for %s (pending approval)",
                    alert.recommended_action.value, item_name)
    except Exception as exc:  # noqa: BLE001
        logger.error("could not propose action for alert: %s", exc)


def _notify(item_name: str, severity: str, summary: str) -> None:
    """Alert side-channel. Best-effort Obsidian note; never fatal."""
    try:
        from services.obsidian import ObsidianService

        ObsidianService().write_alert_note(
            f"**{severity.upper()}** — {item_name}: {summary}"
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("obsidian notify skipped: %s", exc)


def run_cycle() -> None:
    """One pass over all due monitored items."""
    from agents.alert_engine import AlertEngine
    from db import repositories as repo
    from services.reddit import RedditService

    due = repo.items_due_for_check()
    if not due:
        logger.info("no items due")
        return
    logger.info("%d item(s) due for check", len(due))
    reddit = RedditService()
    engine = AlertEngine()
    for row in due:
        try:
            check_item(row, reddit=reddit, engine=engine)
        except Exception as exc:  # noqa: BLE001
            logger.error("check failed for %s: %s", row.get("name"), exc)


def _acquire_singleton_lock():
    """Bind a localhost port as a cross-platform single-instance lock. The
    Startup shortcut fires at every logon; without this, a still-running
    worker plus a fresh logon means duplicate workers and duplicate runs.
    Returns the held socket, or None when another instance already runs."""
    import socket

    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", 47931))
        return s
    except OSError:
        s.close()
        return None


def main() -> None:
    lock = _acquire_singleton_lock()
    if lock is None:
        logger.info("Another monitor worker is already running — exiting")
        return
    logger.info("Monitor worker starting")
    while True:
        try:
            run_cycle()
        except Exception as exc:  # noqa: BLE001
            logger.error("Monitor cycle error: %s", exc)
        time.sleep(POLL_SLEEP_SECONDS)


if __name__ == "__main__":
    main()
