"""x402 Bazaar — discovery of real third-party x402-enabled services.

The Bazaar is Coinbase's public index of live x402 resources (25k+ listings:
paid APIs, data services, tools). Discovery is free and unauthenticated via
the CDP endpoint. These are other people's services on other people's chains:
listings are effectively all mainnet (mostly USDC on Base, some Solana and
Polygon), so PAYING one needs real funds on THAT network — not the XSGD this
agent runs on. Mandate caps protect real spend, and mainnet signing stays off
unless X402_ALLOW_MAINNET is set.

Search quality note: the index's own filters are unreliable (network filter
observed ignored), so filtering happens client-side here.
"""

from __future__ import annotations

import httpx

from core.logging import get_logger

logger = get_logger(__name__)

_DISCOVERY_URL = "https://api.cdp.coinbase.com/platform/v2/x402/discovery/resources"

# Networks our signer can actually pay on (EVM exact scheme). Must track
# payment._signer_client exactly, or a listing gets flagged payable here and
# then fails at signing time.
def payable_networks() -> set[str]:
    from core.config import get_settings
    from services.wallet import is_testnet

    s = get_settings()
    nets = {s.x402_network}
    if s.x402_extra_networks:
        nets.update(n.strip() for n in s.x402_extra_networks.split(",") if n.strip())
    # Mirrors the signer: mainnets are excluded unless explicitly allowed, so
    # a listing is never advertised as payable when the signer would refuse to
    # register for its network.
    if not s.x402_allow_mainnet:
        nets = {n for n in nets if is_testnet(n)}
    return nets


def _parse_item(item: dict) -> dict | None:
    accepts = item.get("accepts") or []
    if not accepts:
        return None
    acc = accepts[0]
    # 1e6 is right here and NOT a stale hardcode: this scales a third party's
    # listing in THEIR asset (Bazaar listings are USDC, 6 decimals), not ours.
    # Our own token's decimals come from wallet.configured_token_decimals.
    try:
        amount_usd = int(acc.get("amount") or acc.get("maxAmountRequired") or 0) / 1_000_000
    except (TypeError, ValueError):
        amount_usd = 0.0
    resource = item.get("resource") or ""
    meta = item.get("metadata") or {}
    return {
        "resource": resource,
        "description": (
            meta.get("description")
            or (item.get("resourceInfo") or {}).get("description")
            or ""
        )[:240],
        "amount_usd": amount_usd,
        "network": acc.get("network", ""),
        "pay_to": acc.get("payTo", ""),
        "payable_by_agent": acc.get("network") in payable_networks()
        and not _is_templated(resource),
        "last_updated": item.get("lastUpdated") or item.get("last_updated") or "",
    }


def _is_templated(resource: str) -> bool:
    """Resources with :param placeholders need caller-supplied path parts —
    not directly payable without more input."""
    return ":" in resource.split("://", 1)[-1] or "{" in resource


def list_services(limit: int = 60, query: str = "") -> list[dict]:
    """Fetch Bazaar listings; optional client-side keyword filter across
    resource URL + description. Sorted cheapest first, payable first."""
    try:
        resp = httpx.get(_DISCOVERY_URL, params={"limit": max(limit, 100)}, timeout=20)
        resp.raise_for_status()
        raw_items = resp.json().get("items", [])
    except Exception as exc:  # noqa: BLE001
        logger.error("bazaar discovery failed: %s", exc)
        raise

    parsed = [p for p in (_parse_item(i) for i in raw_items) if p and p["resource"]]

    if query:
        q = query.lower()
        parsed = [
            p for p in parsed
            if q in p["resource"].lower() or q in p["description"].lower()
        ]

    parsed.sort(key=lambda p: (not p["payable_by_agent"], p["amount_usd"]))
    return parsed[:limit]
