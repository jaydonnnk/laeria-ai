"""Capture an authenticated browser session, without anyone handing over a password.

The five app pages (/decision, /research, /monitor, /commerce, /actions) render
empty without a Supabase session, and the backend additionally requires the
signed-in user's UUID to equal OWNER_USER_ID. So they cannot be verified
headlessly, and a throwaway account will not work either.

This opens a real visible browser, waits for YOU to sign in by hand, and saves
the resulting session to disk. Verification scripts then reuse that file. The
password is typed into the browser and never touches this process, the repo, or
any log.

    python -m scripts.save_login

SECURITY: the saved file contains a live access token. It is gitignored. Treat
it like a password; delete it when you are done (`--clear`).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

AUTH_DIR = Path(__file__).resolve().parent.parent / ".auth"

LOCAL = "http://localhost:3000"
LOGIN_PATH = "/login"
# How long to wait for the human to finish typing.
LOGIN_TIMEOUT_MS = 5 * 60 * 1000


def state_path(site: str) -> Path:
    """Sessions are per-origin — a localhost cookie will not authenticate
    against the deployed domain, so they are stored separately."""
    tag = "local" if "localhost" in site or "127.0.0.1" in site else "prod"
    return AUTH_DIR / f"storage_state.{tag}.json"


def clear(site: str) -> int:
    removed = 0
    for p in AUTH_DIR.glob("storage_state*.json"):
        p.unlink()
        print(f"deleted {p.name}")
        removed += 1
    if not removed:
        print("nothing to delete")
    return 0


def capture(site: str) -> int:
    AUTH_DIR.mkdir(parents=True, exist_ok=True)
    FRONTEND = site
    STATE_PATH = state_path(site)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context(viewport={"width": 1440, "height": 900})
        page = context.new_page()

        try:
            page.goto(f"{FRONTEND}{LOGIN_PATH}", wait_until="domcontentloaded")
        except Exception as exc:  # noqa: BLE001
            print(f"could not reach {FRONTEND} — is the frontend dev server running?")
            print(f"  ({exc})")
            browser.close()
            return 1

        print()
        print("=" * 68)
        print("  A browser window has opened at the login page.")
        print("  Sign in there. Nothing you type is visible to this script.")
        print()
        print("  Waiting for the redirect off /login (up to 5 minutes)...")
        print("=" * 68)
        print()

        try:
            # Successful sign-in navigates away from /login.
            page.wait_for_url(
                lambda url: LOGIN_PATH not in url, timeout=LOGIN_TIMEOUT_MS
            )
        except Exception:  # noqa: BLE001
            print("timed out still on /login — session not saved.")
            print("if the sign-in failed, check the error shown in the browser.")
            browser.close()
            return 1

        page.wait_for_timeout(2500)  # let the session settle into storage

        context.storage_state(path=str(STATE_PATH))
        landed = page.url
        browser.close()

    print(f"saved session -> {STATE_PATH.name}")
    print(f"landed on      {landed}")
    print()
    print("This file holds a live access token. It is gitignored — keep it that")
    print("way, and run `python -m scripts.save_login --clear` when finished.")
    print()
    print("Next: python -m scripts.verify_pages")
    return 0


def refresh(site: str) -> int:
    """Renew an expired access token without another sign-in.

    Supabase access tokens last about an hour, which is shorter than a working
    session. The saved state carries a refresh token and supabase-js renews
    automatically on page load, so loading one page headlessly and re-saving
    is enough — no password, no prompt.
    """
    STATE_PATH = state_path(site)
    if not STATE_PATH.exists():
        print(f"no saved session for {site} — run without --refresh first")
        return 1

    with sync_playwright() as p:
        browser = p.chromium.launch()
        context = browser.new_context(storage_state=str(STATE_PATH))
        page = context.new_page()
        try:
            page.goto(f"{site}/decision", wait_until="domcontentloaded", timeout=60_000)
        except Exception as exc:  # noqa: BLE001
            print(f"could not reach {site}: {exc}")
            browser.close()
            return 1
        page.wait_for_timeout(6000)  # let the client exchange the refresh token
        context.storage_state(path=str(STATE_PATH))
        browser.close()

    # Verify rather than assume. Not bouncing to /login is not evidence the
    # exchange happened — the redirect may simply not have fired yet, and
    # reporting success over a still-expired token sends the caller off
    # debugging the wrong thing entirely.
    remaining = _access_token_lifetime(STATE_PATH)
    if remaining is None:
        print("no access token in the saved state — sign in again:")
        print(f"  python -m scripts.save_login --site {site}")
        return 1
    if remaining <= 0:
        print(f"token is still expired ({int(-remaining)}s ago) — the refresh "
              "token is dead too. Sign in again:")
        print(f"  python -m scripts.save_login --site {site}")
        return 1

    print(f"refreshed {STATE_PATH.name} — valid for another {int(remaining // 60)} min")
    return 0


def _access_token_lifetime(path: Path) -> float | None:
    """Seconds until the stored access token expires; negative if already
    expired, None if there isn't one."""
    import base64
    import time as _time

    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None

    for origin in state.get("origins", []):
        for kv in origin.get("localStorage", []):
            if "auth-token" not in kv.get("name", ""):
                continue
            try:
                token = json.loads(kv["value"])["access_token"]
                payload = token.split(".")[1]
                payload += "=" * (-len(payload) % 4)
                exp = json.loads(base64.urlsafe_b64decode(payload))["exp"]
                return float(exp) - _time.time()
            except Exception:  # noqa: BLE001
                continue
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--clear", action="store_true", help="delete saved sessions")
    ap.add_argument(
        "--refresh",
        action="store_true",
        help="renew an expired token using the saved refresh token (no sign-in)",
    )
    ap.add_argument(
        "--site",
        default=LOCAL,
        help=f"frontend origin to sign in against (default {LOCAL})",
    )
    args = ap.parse_args()
    site = args.site.rstrip("/")
    if args.clear:
        return clear(site)
    if args.refresh:
        return refresh(site)
    return capture(site)


if __name__ == "__main__":
    sys.exit(main())
