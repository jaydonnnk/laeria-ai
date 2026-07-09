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
    print("reddit-signal environment validation (Phase 0.4)\n")

    hard_results = []
    soft_results = []

    # Hard requirements
    from services.reddit import RedditService
    hard_results.append(check("Reddit (PRAW auth + fetch)", RedditService().healthcheck))

    from services.llm import LLMService
    hard_results.append(check("OpenRouter (completion)", LLMService().healthcheck))

    from db.client import healthcheck as supabase_health
    hard_results.append(check("Supabase (connection)", supabase_health))

    # Soft requirement
    from services.obsidian import ObsidianService
    soft_ok = check("Obsidian (Local REST API) [optional]", ObsidianService().healthcheck)
    soft_results.append(soft_ok)

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
