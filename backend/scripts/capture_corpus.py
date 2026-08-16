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

# The least evidence a replay may deliver and still count as working.
#
# Not a confidence target — nothing here asserts high or moderate, and query 3
# is SUPPOSED to come back weak. This asserts only that the corpus still hands
# synthesis real threads to reason over, which is the thing that broke: a
# ranking change made the selector choose threads whose full text was never
# captured, and every one of them failed to fetch. The old check called that a
# pass, because a brief built from zero threads still returns a valid object.
_MIN_THREADS = {
    "best noise cancelling headphones for open office": 5,
    "is the Steam Deck OLED worth it in 2026": 5,
    # Deliberately weak, but "weak" means thin/tangential evidence — not NO
    # evidence. One thread is a broken corpus, not an honest answer.
    "best budget mechanical keyboard for programming under $100": 3,
}


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
    """Prove the corpus can drive the demo with no network at all.

    "Did not raise" is not proof. Every degraded path in the research agent
    returns a perfectly valid ResearchBrief carrying zero threads, so a check
    that only catches exceptions passes just as happily on a corpus that can no
    longer answer anything. This inspects the EVIDENCE instead: threads read,
    sources cited, and a per-query floor.

    Confidence is deliberately not asserted. Requiring a label would create
    pressure to inflate one, and query 3 is supposed to come back weak.
    """
    from agents.research_agent import ResearchAgent
    from services import reddit_fixtures as fx

    if fx.count() == 0:
        print("no fixtures captured — run without --verify first")
        return 1

    ok = 0
    for q in DEMO_QUERIES:
        floor = _MIN_THREADS.get(q, 1)
        try:
            # Cache OFF: a stored brief would let this pass without the corpus
            # being touched at all, which is the opposite of what it proves.
            brief = ResearchAgent().synthesise_decision(q, use_cache=False)
        except Exception as exc:  # noqa: BLE001
            print(f"  FAIL {q}\n       raised: {exc}")
            continue

        threads = brief.signal_quality.thread_count
        sources = len(brief.sources)
        pick = (brief.consensus_pick or "")[:70]
        if threads < floor or sources < floor:
            print(
                f"  FAIL {q}\n"
                f"       {threads} threads / {sources} sources — below the floor of "
                f"{floor}. The corpus can no longer answer this query in full; "
                f"recapture, or check that selection only picks recorded threads.\n"
                f"       note: {brief.signal_quality.bias_notes[:120]}"
            )
            continue
        print(
            f"  ok   {q}\n"
            f"       {threads} threads / {sources} sources / {brief.confidence.value}\n"
            f"       -> {pick or '(no consensus)'}"
        )
        ok += 1

    print(f"\n{ok}/{len(DEMO_QUERIES)} demo queries replay from fixtures with usable evidence")
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
