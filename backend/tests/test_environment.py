"""Phase 0.4 environment validation.

Run before building any Phase 1 feature:
    python -m tests.test_environment

Checks the five external dependencies. Reddit, OpenRouter, and Supabase are
hard requirements (failure blocks progress). Obsidian is a soft requirement
(a warning only — it's optional for Modes 1 and 2).

This is a plain script, not a pytest suite, so it can run on the VPS without
a test runner and give a clear pass/fail summary.
"""

from __future__ import annotations

import sys


def check(name: str, fn) -> bool:
    try:
        ok = fn()
    except Exception as exc:  # noqa: BLE001
        print(f"  [FAIL] {name}: {exc}")
        return False
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
    return ok


def main() -> int:
    print("laeria.ai environment validation (Phase 0.4)\n")

    hard_results = []
    soft_results = []

    # Hard requirements
    from services.reddit import RedditService
    hard_results.append(check("Reddit (old.reddit HTML fetch)", RedditService().healthcheck))

    from services.llm import LLMService
    hard_results.append(check("OpenRouter (completion)", LLMService().healthcheck))

    from db.client import healthcheck as supabase_health
    hard_results.append(check("Supabase (connection)", supabase_health))

    # Soft requirement
    from services.obsidian import ObsidianService
    soft_ok = check("Obsidian (Local REST API) [optional]", ObsidianService().healthcheck)
    soft_results.append(soft_ok)

    # Hackathon prep (card rail + storefront). Soft until those phases land,
    # hard once the demo depends on them — flip lists then.
    from core.config import get_settings
    settings = get_settings()

    # Stripe key only matters when Stripe is the selected issuer (Issuing is
    # unavailable to SG accounts — mock is the pre-event default, StraitsX
    # sandbox arrives at the hackathon).
    if settings.card_issuer == "stripe":
        soft_results.append(check(
            "Stripe key present (STRIPE_API_KEY, test mode) [hackathon]",
            lambda: settings.stripe_api_key.startswith("sk_test_"),
        ))

    def _playwright_launches() -> bool:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            browser.close()
        return True

    soft_results.append(check("Playwright Chromium launches [hackathon]", _playwright_launches))

    def _store_reachable() -> bool:
        import httpx
        url = getattr(settings, "shop_store_url", "")
        if not url:
            return False
        # Password-gated dev stores still answer; any HTTP response = reachable.
        resp = httpx.get(f"{url.rstrip('/')}/products.json", follow_redirects=True, timeout=15)
        return resp.status_code < 500

    soft_results.append(check("Shopify store reachable (SHOP_STORE_URL) [hackathon]", _store_reachable))

    def _card_issuer_ok() -> bool:
        from services.cards import get_issuer
        return get_issuer().healthcheck()

    soft_results.append(check("Card issuer (CARD_ISSUER) [hackathon]", _card_issuer_ok))

    def _wallet_rpc_ok() -> bool:
        from services.wallet import WalletService
        return WalletService().healthcheck()

    soft_results.append(check("Wallet RPC (X402_NETWORK chain) [hackathon]", _wallet_rpc_ok))

    def _mandate_column_exists() -> bool:
        from db.client import get_supabase
        # Selecting the column errors if it doesn't exist (migration 001 not applied).
        get_supabase().table("profiles").select("mandate").limit(1).execute()
        return True

    soft_results.append(check("profiles.mandate column (migration 001) [hackathon]", _mandate_column_exists))

    print()
    if all(hard_results):
        print("All hard requirements PASSED. Cleared to start Phase 1.")
        if not all(soft_results):
            print("Note: Obsidian not reachable — fine unless you're building Mode 3.")
        return 0

    print("Hard requirement(s) FAILED. Fix before starting Phase 1.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
