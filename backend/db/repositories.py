"""Supabase table access for Mode 3 monitoring.

Thin repository layer over the supabase client: every function is a direct
table operation scoped to the signed-in user. The backend runs with the
service-role key (bypasses RLS), so user scoping here is the actual access
control — keep the _owner() filter on every query.

_owner() returns whoever made the current request (see core.current_user), so
two accounts never see each other's rows. Outside a request it falls back to
OWNER_USER_ID, which is what the monitor worker and scripts rely on.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from core.current_user import current_user_id
from core.logging import get_logger
from core.models import MonitorAlert, MonitoredItem, SignalLevel
from db.client import get_supabase

logger = get_logger(__name__)


def _owner() -> str:
    """The user every query in this module is scoped to.

    Named for history — it is the *current* user, not necessarily the
    deployment owner. Kept as-is so the 17 call sites below stay untouched.
    """
    return current_user_id()


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


# ---- mandate (profiles.mandate jsonb) ----

def get_mandate() -> dict:
    """The owner's standing spend rules. Empty dict = nothing authorised."""
    res = (
        get_supabase()
        .table("profiles")
        .select("mandate")
        .eq("id", _owner())
        .limit(1)
        .execute()
    )
    if not res.data:
        # Profile row not created yet — treat as no mandate.
        return {}
    return res.data[0].get("mandate") or {}


def set_mandate(mandate: dict) -> dict:
    res = (
        get_supabase()
        .table("profiles")
        .upsert({"id": _owner(), "mandate": mandate})
        .execute()
    )
    return res.data[0].get("mandate") or {}


# ---- actions ----

def create_action(
    type_: str,
    target: str,
    status: str,
    amount_usd: float,
    metadata: dict[str, Any],
) -> dict:
    row = {
        "user_id": _owner(),
        "type": type_,
        "target": target,
        "status": status,
        "amount_usd": amount_usd,
        "metadata": metadata,
    }
    res = get_supabase().table("actions").insert(row).execute()
    return res.data[0]


def get_action(action_id: str) -> dict | None:
    res = (
        get_supabase()
        .table("actions")
        .select("*")
        .eq("user_id", _owner())
        .eq("id", action_id)
        .limit(1)
        .execute()
    )
    return res.data[0] if res.data else None


def list_actions(limit: int = 50) -> list[dict]:
    return (
        get_supabase()
        .table("actions")
        .select("*")
        .eq("user_id", _owner())
        .order("created_at", desc=True)
        .limit(limit)
        .execute()
        .data
    )


def update_action(action_id: str, fields: dict[str, Any]) -> dict | None:
    res = (
        get_supabase()
        .table("actions")
        .update(fields)
        .eq("user_id", _owner())
        .eq("id", action_id)
        .execute()
    )
    return res.data[0] if res.data else None


# ---- cards (hackathon Issuance pillar) ----

def create_card(
    issuer: str,
    issuer_card_id: str,
    last4: str,
    exp_month: int,
    exp_year: int,
    spend_limit_usd: float,
    status: str = "active",
    action_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict:
    row = {
        "user_id": _owner(),
        "issuer": issuer,
        "issuer_card_id": issuer_card_id,
        "last4": last4,
        "exp_month": exp_month,
        "exp_year": exp_year,
        "spend_limit_usd": spend_limit_usd,
        "status": status,
        "action_id": action_id,
        "metadata": metadata or {},
    }
    res = get_supabase().table("cards").insert(row).execute()
    return res.data[0]


def get_card(card_id: str) -> dict | None:
    res = (
        get_supabase()
        .table("cards")
        .select("*")
        .eq("user_id", _owner())
        .eq("id", card_id)
        .limit(1)
        .execute()
    )
    return res.data[0] if res.data else None


def list_cards(limit: int = 50) -> list[dict]:
    return (
        get_supabase()
        .table("cards")
        .select("*")
        .eq("user_id", _owner())
        .order("created_at", desc=True)
        .limit(limit)
        .execute()
        .data
    )


def update_card_status(card_id: str, status: str) -> dict | None:
    fields: dict[str, Any] = {"status": status}
    if status == "canceled":
        fields["canceled_at"] = datetime.now(timezone.utc).isoformat()
    res = (
        get_supabase()
        .table("cards")
        .update(fields)
        .eq("user_id", _owner())
        .eq("id", card_id)
        .execute()
    )
    return res.data[0] if res.data else None


def executed_spend_this_month() -> float:
    """Sum of executed action amounts since the 1st of the current month —
    the number the mandate's monthly cap is enforced against."""
    now = datetime.now(timezone.utc)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    rows = (
        get_supabase()
        .table("actions")
        .select("amount_usd")
        .eq("user_id", _owner())
        .eq("status", "executed")
        .gte("created_at", month_start.isoformat())
        .execute()
        .data
    )
    return float(sum(float(r["amount_usd"]) for r in rows))
