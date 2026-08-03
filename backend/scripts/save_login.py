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
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

AUTH_DIR = Path(__file__).resolve().parent.parent / ".auth"
STATE_PATH = AUTH_DIR / "storage_state.json"

FRONTEND = "http://localhost:3000"
LOGIN_PATH = "/login"
# How long to wait for the human to finish typing.
LOGIN_TIMEOUT_MS = 5 * 60 * 1000


def clear() -> int:
    if STATE_PATH.exists():
        STATE_PATH.unlink()
        print(f"deleted {STATE_PATH}")
    else:
        print("nothing to delete")
    return 0


def capture() -> int:
    AUTH_DIR.mkdir(parents=True, exist_ok=True)

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

    print(f"saved session -> {STATE_PATH}")
    print(f"landed on      {landed}")
    print()
    print("This file holds a live access token. It is gitignored — keep it that")
    print("way, and run `python -m scripts.save_login --clear` when finished.")
    print()
    print("Next: python -m scripts.verify_pages")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--clear", action="store_true", help="delete the saved session")
    args = ap.parse_args()
    return clear() if args.clear else capture()


if __name__ == "__main__":
    sys.exit(main())
