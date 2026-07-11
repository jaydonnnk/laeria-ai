"""Supabase table access for Mode 3 monitoring.

Thin repository layer over the supabase client: every function is a direct
table operation scoped to the owner user. The backend runs with the
service-role key (bypasses RLS), so user scoping here is the actual access
control — keep the owner_user_id filter on every query.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from core.config import get_settings
from core.logging import get_logger
from core.models import MonitorAlert, MonitoredItem, SignalLevel
from db.client import get_supabase

logger = get_logger(__name__)


def _owner() -> str:
    owner = get_settings().owner_user_id
    if not owner:
        raise RuntimeError(
            "OWNER_USER_ID is not set in .env — create a user in the Supabase "
            "dashboard (Authentication -> Users) and paste its UUID."
        )
    return owner


# ---- monitored_items ----

def create_item(item: MonitoredItem) -> dict:
    row = {
        "user_id": _owner(),
        "name": item.name,
        "category": item.category,
        "subreddits": item.subreddits,
        "check_interval_hours": item.check_interval_hours,
        "action_mandate": item.action_mandate.model_dump(),
        "active": item.active,
    }
    res = get_supabase().table("monitored_items").insert(row).execute()
    return res.data[0]


def list_items(active_only: bool = False) -> list[dict]:
    q = (
        get_supabase()
        .table("monitored_items")
        .select("*")
        .eq("user_id", _owner())
        .order("created_at", desc=True)
    )
    if active_only:
        q = q.eq("active", True)
    return q.execute().data


def get_item(item_id: str) -> dict | None:
    res = (
        get_supabase()
        .table("monitored_items")
        .select("*")
        .eq("user_id", _owner())
        .eq("id", item_id)
        .limit(1)
        .execute()
    )
    return res.data[0] if res.data else None


def delete_item(item_id: str) -> bool:
    res = (
        get_supabase()
        .table("monitored_items")
        .delete()
        .eq("user_id", _owner())
        .eq("id", item_id)
        .execute()
    )
    return bool(res.data)


def set_item_active(item_id: str, active: bool) -> None:
    (
        get_supabase()
        .table("monitored_items")
        .update({"active": active})
        .eq("user_id", _owner())
        .eq("id", item_id)
        .execute()
    )


def touch_item_checked(item_id: str) -> None:
    (
        get_supabase()
        .table("monitored_items")
        .update({"last_checked_at": datetime.now(timezone.utc).isoformat()})
        .eq("id", item_id)
        .execute()
    )


def items_due_for_check() -> list[dict]:
    """Active items whose last check is older than their interval (or never)."""
    due: list[dict] = []
    now = datetime.now(timezone.utc)
    for row in list_items(active_only=True):
        last = row.get("last_checked_at")
        if last is None:
            due.append(row)
            continue
        last_dt = datetime.fromisoformat(last)
        if now - last_dt >= timedelta(hours=row["check_interval_hours"]):
            due.append(row)
    return due


# ---- monitor_runs ----

def create_run(
    item_id: str,
    posts_found: int,
    sentiment: str,
    signal_level: SignalLevel,
    raw_findings: dict[str, Any],
) -> dict:
    row = {
        "item_id": item_id,
        "posts_found": posts_found,
        "sentiment": sentiment,
        "signal_level": signal_level.value,
        "raw_findings": raw_findings,
    }
    res = get_supabase().table("monitor_runs").insert(row).execute()
    return res.data[0]


def recent_runs(item_id: str, limit: int = 10) -> list[dict]:
    """Most recent runs for an item, newest first."""
    return (
        get_supabase()
        .table("monitor_runs")
        .select("*")
        .eq("item_id", item_id)
        .order("ran_at", desc=True)
        .limit(limit)
        .execute()
        .data
    )


# ---- monitor_alerts ----

def create_alert(alert: MonitorAlert) -> dict:
    row = {
        "item_id": alert.item_id,
        "run_id": alert.run_id,
        "severity": alert.severity.value,
        "summary": alert.summary,
        "thread_urls": alert.thread_urls,
        "recommended_action": alert.recommended_action.value,
        "actioned": alert.actioned,
    }
    res = get_supabase().table("monitor_alerts").insert(row).execute()
    return res.data[0]


def list_alerts(limit: int = 50) -> list[dict]:
    """Alerts for all of the owner's items, newest first."""
    item_ids = [r["id"] for r in list_items()]
    if not item_ids:
        return []
    return (
        get_supabase()
        .table("monitor_alerts")
        .select("*")
        .in_("item_id", item_ids)
        .order("created_at", desc=True)
        .limit(limit)
        .execute()
        .data
    )


def mark_alert_actioned(alert_id: str) -> None:
    (
        get_supabase()
        .table("monitor_alerts")
        .update({"actioned": True})
        .eq("id", alert_id)
        .execute()
    )
