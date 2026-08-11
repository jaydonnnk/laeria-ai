"""Demo storefront access (Shopify dev store) — the hackathon Discovery pillar.

Two layers, deliberately split:
- HTTP (`/products.json`) is the data backbone: fast, structured, reliable.
  Keyword filtering happens client-side (same reasoning as bazaar.py — trust
  our own filter over a search endpoint's quirks).
- Playwright drives a real browser to the product page for *verification*:
  re-reads price/availability from the live DOM and captures a screenshot for
  the audit trail. This is the visible "agent scans the site" beat on stage,
  and the DOM price is a check the checkout executor (Phase 3) repeats before
  it ever touches card fields.

Dev stores are password-gated; both layers unlock with the storefront
password from config (cookie for HTTP, form fill for the browser).
"""

from __future__ import annotations

import base64
import time
from pathlib import Path

import httpx
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from core.config import get_settings
from core.logging import get_logger

logger = get_logger(__name__)

# Same browser UA discipline as reddit.py — look like a browser, not a bot.
_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

_SCREENSHOT_DIR = Path(__file__).resolve().parent.parent / "screenshots" / "store"


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in (429, 500, 502, 503, 504)
    return isinstance(exc, (httpx.TimeoutException, httpx.TransportError))


class StorefrontService:
    def __init__(self) -> None:
        settings = get_settings()
        self._settings = settings
        self._base = settings.shop_store_url.rstrip("/")
        self._client = httpx.Client(
            base_url=self._base,
            headers={"User-Agent": _BROWSER_UA},
            timeout=20.0,
            follow_redirects=True,
        )
        self._unlocked = False

    # ---- HTTP layer ----

    def _unlock_http(self) -> None:
        """Dev stores are password-gated. Submitting the storefront password
        once sets a session cookie on the client; idempotent if already open."""
        if self._unlocked or not self._settings.shop_storefront_password:
            self._unlocked = True
            return
        try:
            self._client.get("/password")
            self._client.post(
                "/password",
                data={
                    "form_type": "storefront_password",
                    "utf8": "✓",
                    "password": self._settings.shop_storefront_password,
                },
            )
        except Exception as exc:  # noqa: BLE001
            # products.json often serves even while gated — don't fail discovery
            # over the unlock; the browser layer unlocks separately anyway.
            logger.warning("storefront password unlock failed: %s", exc)
        self._unlocked = True

    @retry(
        retry=retry_if_exception(_is_retryable),
        wait=wait_exponential(multiplier=1, min=1, max=15),
        stop=stop_after_attempt(3),
        reraise=True,
    )
    def _get(self, path: str, params: dict | None = None) -> httpx.Response:
        self._unlock_http()
        resp = self._client.get(path, params=params)
        from core.usage import incr

        incr("store_requests")
        resp.raise_for_status()
        return resp

    def search_products(self, query: str = "", limit: int = 12) -> list[dict]:
        """Full catalogue via /products.json, keyword-filtered client-side
        across title/type/tags/vendor. Available first, then cheapest —
        mirrors bazaar.list_services (payable first, cheapest first)."""
        raw = self._get("/products.json", params={"limit": 250}).json()
        products = [
            p for p in (self._parse_product(item) for item in raw.get("products", []))
            if p
        ]

        if query:
            q = query.lower()
            products = [
                p for p in products
                if q in p["title"].lower()
                or q in p["product_type"].lower()
                or q in p["tags"].lower()
                or q in p["vendor"].lower()
            ]

        products.sort(key=lambda p: (not p["available"], p["price_usd"]))
        return products[:limit]

    def get_product(self, handle: str) -> dict | None:
        """One product by handle, fetched directly.

        A handle is an identifier, so it deserves a lookup rather than a scan.
        The caller that needed this was filtering a *paginated listing* for a
        known handle, which quietly returns "not found" for any product past
        the page limit — correct on a 13-product dev store, wrong on any real
        catalogue. Returns None when the store has no such product.
        """
        try:
            raw = self._get(f"/products/{handle}.json").json()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                return None
            raise
        return self._parse_product(raw.get("product") or {})

    def _parse_product(self, item: dict) -> dict | None:
        variants = item.get("variants") or []
        if not variants:
            return None
        v = variants[0]
        try:
            price = float(v.get("price") or 0)
        except (TypeError, ValueError):
            price = 0.0
        images = item.get("images") or []
        handle = item.get("handle") or ""
        return {
            "id": str(item.get("id", "")),
            "handle": handle,
            "title": item.get("title", ""),
            "price_usd": price,
            "url": f"{self._base}/products/{handle}",
            "image": (images[0].get("src") if images else "") or "",
            "available": bool(v.get("available")),
            "variant_id": str(v.get("id", "")),
            "product_type": item.get("product_type") or "",
            "vendor": item.get("vendor") or "",
            "tags": " ".join(item.get("tags", []))
            if isinstance(item.get("tags"), list)
            else str(item.get("tags") or ""),
        }

    # ---- Browser layer (Playwright) ----

    def _launch_kwargs(self) -> dict:
        kwargs: dict = {"headless": not self._settings.browser_headed}
        if self._settings.playwright_channel == "chrome":
            kwargs["channel"] = "chrome"
        return kwargs

    def _session_cookies(self) -> list[dict]:
        """Unlock over HTTP, then hand the session cookies (incl. the
        password-gate `storefront_digest`) to the browser context — more
        deterministic than driving the password form in the browser."""
        self._unlock_http()
        domain = self._base.split("://", 1)[-1]
        return [
            {
                "name": c.name,
                "value": c.value or "",
                "domain": c.domain or domain,
                "path": c.path or "/",
            }
            for c in self._client.cookies.jar
        ]

    def verify_product(self, handle: str) -> dict:
        """Open the live product page in a real browser, re-read price and
        availability from the DOM, and screenshot it for the audit trail.
        The DOM price is authoritative over the JSON listing — it's what a
        human buyer would see, and what checkout will charge."""
        from playwright.sync_api import sync_playwright

        url = f"{self._base}/products/{handle}"
        _SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
        shot_path = _SCREENSHOT_DIR / f"{handle}-{int(time.time() * 1000)}.png"

        with sync_playwright() as p:
            browser = p.chromium.launch(**self._launch_kwargs())
            try:
                context = browser.new_context(
                    viewport={"width": 1280, "height": 900}, user_agent=_BROWSER_UA
                )
                context.add_cookies(self._session_cookies())
                page = context.new_page()
                page.goto(url, wait_until="domcontentloaded", timeout=30000)
                if "/password" in page.url:
                    raise RuntimeError(
                        "storefront password gate not cleared — check "
                        "SHOP_STOREFRONT_PASSWORD"
                    )

                dom_price = self._dom_price(page)
                available = self._dom_available(page)
                page.screenshot(path=str(shot_path))
            finally:
                browser.close()

        shot_b64 = base64.b64encode(shot_path.read_bytes()).decode("ascii")
        return {
            "handle": handle,
            "url": url,
            "price_usd": dom_price,
            "available": available,
            "screenshot_path": str(shot_path),
            "screenshot_data_url": f"data:image/png;base64,{shot_b64}",
        }

    @staticmethod
    def _dom_price(page) -> float:  # noqa: ANN001
        """Shopify themes expose the canonical price as an og:price:amount
        meta tag — theme-markup-independent, present in <head>."""
        content = page.locator('meta[property="og:price:amount"]').first.get_attribute(
            "content"
        )
        try:
            return float((content or "0").replace(",", ""))
        except ValueError:
            return 0.0

    @staticmethod
    def _dom_available(page) -> bool:  # noqa: ANN001
        """The add-to-cart button ([name=add]) is disabled on sold-out
        products across stock Shopify themes."""
        button = page.locator("button[name='add']").first
        try:
            return button.count() > 0 and not button.is_disabled()
        except Exception:  # noqa: BLE001
            return False

    def healthcheck(self) -> bool:
        """Store reachable and catalogue non-empty. Used by test_environment."""
        try:
            return len(self.search_products(limit=1)) > 0
        except Exception as exc:  # noqa: BLE001
            logger.error("storefront healthcheck failed: %s", exc)
            return False
