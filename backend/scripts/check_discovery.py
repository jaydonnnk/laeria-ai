"""Preflight for live community discovery.

    python -m scripts.check_discovery
    python -m scripts.check_discovery --query "Are IKEA LADDA batteries worth it?"

Three cheap checks and no paid work: no OpenRouter call, no synthesis, no
payment, and — by design — no request to reddit.com. Run it on Render right
after a deploy to find out whether discovery is actually wired up, rather than
discovering it mid-demo.

Mirrors scripts/check_guardrails.py: presence of a key is reported, never its
value.
"""

from __future__ import annotations

import argparse
import sys
import time

DEFAULT_QUERY = "Are IKEA LADDA rechargeable batteries worth buying?"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--query", default=DEFAULT_QUERY)
    args = ap.parse_args()

    from agents import relevance
    from core.config import get_settings
    from services import discovery

    s = get_settings()
    print("laeria.ai discovery preflight\n")
    print(f"  provider   : {s.discovery_provider or 'none'}")
    print(f"  api key    : {'configured' if s.discovery_api_key else 'MISSING'}")
    print(f"  candidates : {s.discovery_candidates}")
    print()

    source = discovery.get_source()
    ok, detail = source.available()
    if not ok:
        print(f"  [FAIL] {detail}")
        print("\n  discovery is OFF — Laeria cannot find new discussions.")
        return 1
    print(f"  [PASS] provider configured: {detail}")

    started = time.time()
    try:
        candidates = source.search(args.query, limit=s.discovery_candidates)
    except Exception as exc:  # noqa: BLE001
        print(f"  [FAIL] search raised: {type(exc).__name__}: {str(exc)[:120]}")
        return 1
    ms = (time.time() - started) * 1000

    if not candidates:
        print(f"  [FAIL] search returned no Reddit discussions [{ms:.0f}ms]")
        print("\n  the provider answered but found nothing — check the query or quota.")
        return 1
    print(f"  [PASS] search -> {len(candidates)} Reddit discussions [{ms:.0f}ms]")

    # Ranking is local arithmetic; proving it runs here means the demo path is
    # whole, not just the network leg.
    threads = [discovery.to_thread(c) for c in candidates]
    weights = relevance.build_weights(args.query, [], threads)
    ranked = sorted(candidates, key=lambda c: -relevance.score(discovery.to_thread(c), weights))
    print(f"  [PASS] relevance ranked {len(ranked)} candidates\n")

    for c in ranked[:5]:
        score = relevance.score(discovery.to_thread(c), weights)
        title = "".join(ch for ch in c.title if ch.isprintable())[:62]
        print(f"    rel={score:6.2f}  r/{c.subreddit or '?':<18} {title}")

    print("\n  discovery is live. Full discussions still require the browser")
    print("  extension or the recorded corpus — Reddit refuses this server.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
