"""Render every authed page with a real session and report what is broken.

These five pages have never been seen with real data — they typecheck and the
production build passes, which proves neither. This loads each one with the
session captured by scripts/save_login, and reports console errors, failed
network requests, auth failures, and empty states, with a screenshot each.

    python -m scripts.save_login      # once, by hand
    python -m scripts.verify_pages

Exit code is non-zero if any page had an error, so this can gate a rehearsal.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

AUTH_DIR = Path(__file__).resolve().parent.parent / ".auth"
SHOT_DIR = AUTH_DIR / "shots"

LOCAL = "http://localhost:3000"


def state_path(site: str) -> Path:
    tag = "local" if "localhost" in site or "127.0.0.1" in site else "prod"
    return AUTH_DIR / f"storage_state.{tag}.json"

PAGES = [
    ("/decision", "What to buy"),
    ("/research", "How it went"),
    ("/monitor", "Monitor"),
    ("/commerce", "Commerce"),
    ("/actions", "Actions"),
]

# Requests to these paths returning 401/403 mean the session or OWNER_USER_ID
# is wrong — a different failure from a page bug, and worth calling out.
AUTH_STATUSES = {401, 403}


def check_page(context, path: str, label: str, viewport: str, site: str) -> dict:
    page = context.new_page()
    console_errors: list[str] = []
    page_errors: list[str] = []
    failed: list[str] = []
    auth_failures: list[str] = []

    page.on(
        "console",
        lambda m: console_errors.append(m.text[:200]) if m.type == "error" else None,
    )
    page.on("pageerror", lambda e: page_errors.append(str(e)[:200]))

    def on_response(resp):
        if resp.status >= 400:
            entry = f"{resp.status} {resp.url[:110]}"
            failed.append(entry)
            if resp.status in AUTH_STATUSES:
                auth_failures.append(entry)

    page.on("response", on_response)

    try:
        page.goto(f"{site}{path}", wait_until="networkidle", timeout=60_000)
        page.wait_for_timeout(2500)
    except Exception as exc:  # noqa: BLE001
        page_errors.append(f"navigation failed: {exc}")

    # Did the app bounce us back to login? That means the session is not valid.
    bounced = "/login" in page.url

    body_text = ""
    try:
        body_text = page.inner_text("body")[:4000]
    except Exception:  # noqa: BLE001
        pass

    SHOT_DIR.mkdir(parents=True, exist_ok=True)
    shot = SHOT_DIR / f"{viewport}-{path.strip('/')}.png"
    try:
        page.screenshot(path=str(shot), full_page=True)
    except Exception:  # noqa: BLE001
        shot = None

    page.close()
    return {
        "path": path,
        "label": label,
        "bounced": bounced,
        "console_errors": console_errors,
        "page_errors": page_errors,
        "failed": failed,
        "auth_failures": auth_failures,
        "text_len": len(body_text.strip()),
        "shot": shot,
    }


def report(results: list[dict]) -> int:
    bad = 0
    print()
    print("=" * 72)
    for r in results:
        problems = []
        if r["bounced"]:
            problems.append("BOUNCED TO /login — session invalid or expired")
        if r["auth_failures"]:
            problems.append(
                f"{len(r['auth_failures'])} auth failure(s) — signed-in user is "
                "probably not OWNER_USER_ID"
            )
        other_failed = [f for f in r["failed"] if f not in r["auth_failures"]]
        if other_failed:
            problems.append(f"{len(other_failed)} failed request(s)")
        if r["page_errors"]:
            problems.append(f"{len(r['page_errors'])} uncaught JS error(s)")
        if r["console_errors"]:
            problems.append(f"{len(r['console_errors'])} console error(s)")
        if r["text_len"] < 200 and not r["bounced"]:
            problems.append(f"page nearly empty ({r['text_len']} chars of text)")

        status = "FAIL" if problems else "ok  "
        if problems:
            bad += 1
        print(f"{status}  {r['path']:<12} {r['label']}")
        for p in problems:
            print(f"        - {p}")
        for f in (r["failed"] or [])[:5]:
            print(f"          {f}")
        for e in (r["page_errors"] or [])[:3]:
            print(f"          JS: {e}")
        for e in (r["console_errors"] or [])[:3]:
            print(f"          console: {e}")
    print("=" * 72)
    print(f"{len(results) - bad}/{len(results)} pages clean")
    if SHOT_DIR.exists():
        print(f"screenshots: {SHOT_DIR}")
    return 1 if bad else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--mobile", action="store_true", help="also check at 390x844")
    ap.add_argument("--site", default=LOCAL, help=f"origin to check (default {LOCAL})")
    args = ap.parse_args()

    site = args.site.rstrip("/")
    STATE_PATH = state_path(site)
    if not STATE_PATH.exists():
        print(f"no saved session for {site} ({STATE_PATH.name})")
        print(f"run `python -m scripts.save_login --site {site}` first.")
        return 1
    print(f"checking {site}")

    viewports = [("desktop", {"width": 1440, "height": 900})]
    if args.mobile:
        viewports.append(("mobile", {"width": 390, "height": 844}))

    results: list[dict] = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for name, vp in viewports:
            context = browser.new_context(
                storage_state=str(STATE_PATH), viewport=vp
            )
            print(f"\n--- {name} {vp['width']}x{vp['height']} ---")
            for path, label in PAGES:
                print(f"  checking {path} ...")
                results.append(check_page(context, path, label, name, site))
            context.close()
        browser.close()

    return report(results)


if __name__ == "__main__":
    sys.exit(main())
