"""Demo reset + rehearsal runner (hackathon Phase 6).

Run before every rehearsal / the stage demo so the run starts clean:

    python -m scripts.demo_e2e            # reset + healthchecks only
    python -m scripts.demo_e2e --buy      # + one full card-rail purchase

Reset: cancels stray active cards (issuer + DB), sweeps pending actions.
Checks: storefront, card issuer, wallet RPC, Playwright launch.
--buy: runs the real propose→execute pipeline against DEMO_PRODUCT. It
temporarily raises the mandate so the run executes autonomously and ALWAYS
restores the original in a finally — the override is printed loudly.

Exit code 0 = ready for stage.
"""

from __future__ import annotations

import argparse
import sys

DEMO_PRODUCT_HANDLE = "selling-plans-ski-wax"
DEMO_VARIANT_ID = "52099303375146"


def _reset() -> bool:
    from db import repositories as repo
    from services.cards import get_issuer

    ok = True
    issuer = get_issuer()
    stray = [c for c in repo.list_cards(limit=50) if c["status"] != "canceled"]
    for card in stray:
        try:
            issuer.cancel(card["issuer_card_id"])
            repo.update_card_status(card["id"], "canceled")
            print(f"  canceled stray card ••••{card['last4']}")
        except Exception as exc:  # noqa: BLE001
            print(f"  [WARN] could not cancel card {card['id']}: {exc}")
            ok = False

    pending = [a for a in repo.list_actions(limit=50) if a["status"] == "pending_approval"]
    for action in pending:
        repo.update_action(
            action["id"],
            {"status": "cancelled",
             "metadata": {**(action.get("metadata") or {}), "demo_reset": True}},
        )
        print(f"  swept pending action {action['id'][:8]}…")
    print(f"  reset done ({len(stray)} cards, {len(pending)} pending actions)")
    return ok


def _checks() -> bool:
    from services.cards import get_issuer
    from services.storefront import StorefrontService

    results = []

    def check(name: str, fn) -> None:
        try:
            ok = bool(fn())
        except Exception as exc:  # noqa: BLE001
            print(f"  [FAIL] {name}: {exc}")
            results.append(False)
            return
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
        results.append(ok)

    check("storefront (search + catalogue)", StorefrontService().healthcheck)
    check("card issuer", get_issuer().healthcheck)

    def _pw() -> bool:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            p.chromium.launch(headless=True).close()
        return True

    check("playwright chromium", _pw)

    # The chain side prints its own per-check lines: "does the RPC answer" was
    # never the question — gas, the token's identity and the facilitator are.
    from scripts.check_chain import run as chain_run

    print("  chain:")
    results.append(chain_run())
    return all(results)


def _buy() -> bool:
    from api.routes.actions import ProposeRequest, propose_action
    from db import repositories as repo

    original = repo.get_mandate()
    print("  [OVERRIDE] raising mandate for the rehearsal run "
          "(restored automatically)")
    repo.set_mandate({
        **original,
        "max_per_transaction": 100.0,
        # An explicit figure, not 0.0 — an unset/zero cap is ZERO ALLOWANCE
        # now, not unlimited (see ActionMandate).
        "max_per_month": 500.0,
        "require_confirmation_above": 100.0,
        "autonomous_actions_enabled": True,
    })
    try:
        res = propose_action(ProposeRequest(
            type="purchase",
            target_url=f"https://baryon-ai.myshopify.com/products/{DEMO_PRODUCT_HANDLE}",
            category="commerce",
            description="[Demo rehearsal] ski wax",
            rail="card",
            product_handle=DEMO_PRODUCT_HANDLE,
            variant_id=DEMO_VARIANT_ID,
        ))
        action = res["action"]
        meta = action["metadata"]
        print(f"  outcome: {res['outcome']}")
        print(f"  status:  {action['status']} | ${action['amount_usd']}")
        print(f"  order:   {meta.get('order_reference')} | card ••••{meta.get('card_last4')}")
        return action["status"] == "executed"
    except Exception as exc:  # noqa: BLE001
        print(f"  [FAIL] rehearsal purchase: {exc}")
        return False
    finally:
        repo.set_mandate(original)
        print("  [OVERRIDE] mandate restored")


def main() -> int:
    parser = argparse.ArgumentParser(description="demo reset + rehearsal")
    parser.add_argument("--buy", action="store_true",
                        help="also run one full card-rail purchase")
    args = parser.parse_args()

    print("laeria.ai demo prep\n")
    print("reset:")
    ok = _reset()
    print("checks:")
    ok = _checks() and ok
    if args.buy:
        print("rehearsal purchase:")
        ok = _buy() and ok

    print()
    print("READY FOR STAGE" if ok else "NOT READY — fix the failures above")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
