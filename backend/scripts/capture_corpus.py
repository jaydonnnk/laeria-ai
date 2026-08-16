"""Freeze a Reddit corpus for the demo, while logged-out access still works.

Reddit announced on 2026-06-30 that old.reddit.com will require a login,
rolling out "over the next month", specifically to stop logged-out automated
traffic. That window contains demo day. Run this NOW; every day it is not run
is a day the corpus might become uncapturable.

    python -m scripts.capture_corpus                 # capture the demo queries
    python -m scripts.capture_corpus --query "..."   # add one more
    python -m scripts.capture_corpus --verify        # replay-only smoke test

Capture forces REDDIT_SOURCE=record regardless of .env, so it always writes.
Verification forces REDDIT_SOURCE=fixture, so a pass proves the demo can run
with the network unplugged.
"""

from __future__ import annotations

import argparse
import os
import sys
import time

# Must be set before anything imports the cached Settings.
_MODE_ENV = "REDDIT_SOURCE"


def _reset_settings() -> None:
    from core.config import get_settings

    get_settings.cache_clear()


# The queries the demo actually runs. Keep this list short — every entry costs
# ~20-27 paced requests (about 30-40s each) and the point is a demo corpus,
# not a mirror of Reddit.
# Order matters — this is the demo running order.
#   1-2 return a strong consensus pick (the happy path, and the one that hands
#       off to checkout).
#   3   deliberately returns LOW confidence: the threads it finds are only
#       tangential, so it says so instead of inventing a pick. Keep it. An
#       agent that admits it does not know is the differentiator over asking
#       a chatbot, and it is far more persuasive demonstrated than claimed.
DEMO_QUERIES = [
    "best noise cancelling headphones for open office",
    "is the Steam Deck OLED worth it in 2026",
    "best budget mechanical keyboard for programming under $100",
]


def capture(queries: list[str]) -> int:
    from agents.research_agent import ResearchAgent
    from services import reddit_fixtures as fx

    before = fx.count()
    for i, q in enumerate(queries, 1):
        print(f"[{i}/{len(queries)}] capturing: {q}")
        started = time.time()
        try:
            # Run the real research path so every request it makes is recorded
            # exactly as the demo will replay it.
            ResearchAgent().synthesise_decision(q)
        except Exception as exc:  # noqa: BLE001
            # A partial capture is still useful — the requests that succeeded
            # before the failure are on disk.
            print(f"    ! failed after {time.time() - started:.0f}s: {exc}")
            continue
        print(f"    done in {time.time() - started:.0f}s")

    after = fx.count()
    print(f"\nfixtures: {before} -> {after} (+{after - before})")
    print(f"corpus captured_at: {fx.captured_at()}")
    return 0 if after > before else 1


def verify() -> int:
    """Prove the corpus can drive the demo with no network at all."""
    from agents.research_agent import ResearchAgent
    from services import reddit_fixtures as fx

    if fx.count() == 0:
        print("no fixtures captured — run without --verify first")
        return 1

    ok = 0
    for q in DEMO_QUERIES:
        try:
            brief = ResearchAgent().synthesise_decision(q)
        except Exception as exc:  # noqa: BLE001
            print(f"  FAIL {q}\n       {exc}")
            continue
        pick = (brief.consensus_pick or "")[:70]
        print(f"  ok   {q}\n       -> {pick or '(no consensus)'}")
        ok += 1

    print(f"\n{ok}/{len(DEMO_QUERIES)} demo queries replay from fixtures alone")
    return 0 if ok == len(DEMO_QUERIES) else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--query", action="append", default=[],
                    help="extra query to capture (repeatable)")
    ap.add_argument("--verify", action="store_true",
                    help="replay-only smoke test; makes no network calls")
    args = ap.parse_args()

    if args.verify:
        os.environ[_MODE_ENV] = "fixture"
        _reset_settings()
        return verify()

    os.environ[_MODE_ENV] = "record"
    _reset_settings()
    queries = args.query or DEMO_QUERIES
    print(f"REDDIT_SOURCE=record — capturing {len(queries)} query set(s)\n")
    return capture(queries)


if __name__ == "__main__":
    sys.exit(main())
