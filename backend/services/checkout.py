"""Checkout executor — the hackathon Execution pillar.

Drives the Shopify checkout in a real browser with the issued disposable
card. The mandate ceiling is enforced INSIDE the checkout: after the checkout
page renders, the order total is re-read from the live DOM and compared
against the approved ceiling BEFORE any card field is touched — the third
verification layer (propose-time listing price, execute-time DOM price,
checkout-page total).

Gateway profiles (config CHECKOUT_GATEWAY_PROFILE):
- "bogus": Shopify's Bogus Gateway test processor accepts only magic PANs
  (1 = success). The magic PAN is entered instead of the issued card's real
  number, and the shim is declared in the result metadata — the issued card
  remains the card of record and (on issuers that support it) a matching
  authorization is simulated so the card shows the real transaction.
- "real": the issued card's actual PAN is entered. Same code path — this is
  the one-line flip for the StraitsX sandbox at the event.

Screenshots (checkout loaded / pre-submit / confirmation) are written to
backend/screenshots/checkout/ for the audit trail.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from pathlib import Path

from core.config import get_settings
from core.logging import get_logger
from services.cards import CardDetails

logger = get_logger(__name__)

_SCREENSHOT_DIR = Path(__file__).resolve().parent.parent / "screenshots" / "checkout"

_BOGUS_SUCCESS_PAN = "1"

# Memory-frugal Chromium flags for constrained containers. --disable-dev-shm-usage
# is the load-bearing one: /dev/shm is tiny in most containers, and without this
# Chromium crashes (or the process is OOM-killed) partway through a heavy page,
# which surfaces as a checkout that hangs with no response. --no-sandbox is
# required in many container runtimes. These shrink the footprint but do NOT make
# a 512MB instance enough — Chromium on a Shopify checkout wants ~1GB+.
CHROMIUM_ARGS = [
    "--disable-dev-shm-usage",
    "--no-sandbox",
    "--disable-gpu",
    "--disable-extensions",
]


class CheckoutError(RuntimeError):
    pass


class CheckoutCeilingViolation(CheckoutError):
    """Checkout-page total exceeds the approved ceiling — refused before the
    card was touched."""


@dataclass
class ShippingProfile:
    name: str
    email: str
    address1: str
    city: str
    postal_code: str
    country_code: str  # ISO-3166 alpha-2, e.g. "US"
    zone_code: str = ""  # state/province where the country needs one


@dataclass
class CheckoutResult:
    order_reference: str
    total_usd: float
    gateway_profile: str
    pan_shim: bool  # True when the Bogus magic PAN replaced the real one
    screenshots: list[str]


def shipping_from_settings() -> ShippingProfile:
    s = get_settings()
    return ShippingProfile(
        name=s.shipping_name,
        email=s.shipping_email,
        address1=s.shipping_address1,
        city=s.shipping_city,
        postal_code=s.shipping_postal_code,
        country_code=s.shipping_country,
        zone_code=s.shipping_zone,
    )


# Fields the checkout cannot proceed without. zone_code is optional — only
# countries with states/provinces need one.
SHIPPING_REQUIRED = ("name", "email", "address1", "city", "postal_code", "country_code")


def shipping_is_complete(data: dict | None) -> bool:
    """True when every required field is present and non-blank."""
    if not data:
        return False
    return all(str(data.get(f) or "").strip() for f in SHIPPING_REQUIRED)


def shipping_for_current_user() -> ShippingProfile:
    """The address the agent ships to for the signed-in user.

    Prefers the user's saved profile; falls back to the env SHIPPING_* profile
    when the account has not filled one in (or filled it only partway). The
    fallback is all-or-nothing on purpose: merging a user's street with the env
    city would ship to an address nobody actually chose.
    """
    from db import repositories as repo

    try:
        saved = repo.get_shipping()
    except Exception:  # noqa: BLE001 - a profile read failure must not block checkout
        saved = None
    if shipping_is_complete(saved):
        return ShippingProfile(
            name=saved["name"],
            email=saved["email"],
            address1=saved["address1"],
            city=saved["city"],
            postal_code=saved["postal_code"],
            country_code=saved["country_code"],
            zone_code=saved.get("zone_code", "") or "",
        )
    return shipping_from_settings()


# A money gate that only understands one currency is a money gate that stops
# working the moment the store is repriced. The old pattern hardcoded `$`, so
# an SGD store returned None on every read — safe, but the checkout could
# never run at all. Currency markers are listed most-specific-first so "S$"
# wins over a bare "$".
_CURRENCY = (
    r"(?:S\$|R\$|HK\$|NT\$|A\$|C\$|NZ\$|[$€£¥₹]|"
    r"(?:SGD|USD|EUR|GBP|MYR|AUD|CAD|NZD|HKD|JPY|INR|CNY)\b)"
)
# Cents are required. A total is always written with them, and demanding them
# is what stops "Total 2 items" from being read as a price.
_MONEY_RE = re.compile(_CURRENCY + r"\s*([\d,]+\.\d{2})")

# How far past the label to look. Wide enough for "Total  SGD $24.95" with
# markup-derived whitespace between, tight enough not to reach the next row
# of the order summary.
_MONEY_WINDOW = 120


def _money_after(label_pattern: str, text: str) -> float | None:
    """Find the first currency amount following a label ("Total", ...) in
    rendered page text. Theme-markup-independent and currency-independent by
    design; returns None when no *currency-marked* amount follows the label,
    which the callers treat as "refuse to proceed"."""
    label = re.search(label_pattern, text, re.IGNORECASE)
    if not label:
        return None
    m = _MONEY_RE.search(text[label.end() : label.end() + _MONEY_WINDOW])
    if not m:
        return None
    try:
        return float(m.group(1).replace(",", ""))
    except ValueError:
        return None


def execute_checkout(
    variant_id: str,
    card: CardDetails,
    shipping: ShippingProfile,
    mandate_ceiling_usd: float,
    session_cookies: list[dict],
) -> CheckoutResult:
    """Add the variant to the cart via permalink, walk the checkout, verify
    the DOM total against the ceiling, pay with the card, and return the
    order reference. Raises CheckoutCeilingViolation before card entry if the
    live total exceeds the approved ceiling."""
    from playwright.sync_api import sync_playwright

    settings = get_settings()
    base = settings.shop_store_url.rstrip("/")
    profile = settings.checkout_gateway_profile
    _SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = int(time.time())
    shots: list[str] = []

    def shot(page, name: str) -> None:  # noqa: ANN001
        if page is None:  # failed before the page existed
            return
        path = _SCREENSHOT_DIR / f"{stamp}-{name}.png"
        try:
            page.screenshot(path=str(path))
            shots.append(str(path))
        except Exception as exc:  # noqa: BLE001
            logger.warning("checkout screenshot %s failed: %s", name, exc)

    launch_kwargs: dict = {
        "headless": not settings.browser_headed,
        "args": CHROMIUM_ARGS,
    }
    if settings.playwright_channel == "chrome":
        launch_kwargs["channel"] = "chrome"

    with sync_playwright() as p:
        browser = p.chromium.launch(**launch_kwargs)
        page = None
        try:
            context = browser.new_context(viewport={"width": 1280, "height": 950})
            context.add_cookies(session_cookies)
            page = context.new_page()

            # Cart permalink: adds the variant and (on current Shopify) drops
            # straight into the one-page checkout — no add-to-cart clicking.
            page.goto(f"{base}/cart/{variant_id}:1", wait_until="domcontentloaded", timeout=45000)
            if "/password" in page.url:
                raise CheckoutError("storefront password gate not cleared")

            # Older flows land on the cart instead — click through if so.
            if "/checkouts/" not in page.url:
                page.locator(
                    "button[name='checkout'], input[name='checkout']"
                ).first.click(timeout=15000)
                page.wait_for_url(re.compile(r"/checkouts/"), timeout=45000)
            page.wait_for_load_state("domcontentloaded")
            page.wait_for_timeout(2000)  # checkout app hydrates client-side
            shot(page, "checkout-loaded")

            # ---- Ceiling gate: read the live total BEFORE touching the card.
            body_text = page.locator("body").inner_text()
            total = _money_after(r"\bTotal\b", body_text)
            if total is None:
                raise CheckoutTotalUnreadable(body_text)
            if total > mandate_ceiling_usd + 1e-9:
                raise CheckoutCeilingViolation(
                    f"checkout total ${total:.2f} exceeds approved ceiling "
                    f"${mandate_ceiling_usd:.2f}"
                )

            # ---- Contact + shipping. Selectors target Shopify's stable
            # autocomplete/name attributes, not theme markup.
            _fill_if_present(page, "input[name='email'], input#email", shipping.email)
            first, _, last = shipping.name.partition(" ")
            _fill_if_present(page, "input[name='firstName']", first)
            _fill_if_present(page, "input[name='lastName']", last or first)
            _fill_if_present(page, "input[name='address1']", shipping.address1)
            _select_if_present(page, "select[name='countryCode']", shipping.country_code)
            _fill_if_present(page, "input[name='city']", shipping.city)
            _fill_if_present(page, "input[name='postalCode']", shipping.postal_code)
            _select_if_present(page, "select[name='zone']", shipping.zone_code)
            # Let the checkout recompute shipping methods for the address.
            page.wait_for_timeout(2500)

            # ---- Ceiling gate #2: shipping/taxes have now been computed for
            # the address — re-read the FINAL total and re-check before any
            # card field is touched. This number is the one recorded.
            body_text = page.locator("body").inner_text()
            final_total = _money_after(r"\bTotal\b", body_text)
            if final_total is None:
                raise CheckoutTotalUnreadable(body_text)
            total = final_total
            if total > mandate_ceiling_usd + 1e-9:
                raise CheckoutCeilingViolation(
                    f"final checkout total ${total:.2f} (incl. shipping/taxes) "
                    f"exceeds approved ceiling ${mandate_ceiling_usd:.2f}"
                )

            # ---- Card fields (iframed). Bogus profile swaps in the magic PAN.
            pan_shim = profile == "bogus"
            number = _BOGUS_SUCCESS_PAN if pan_shim else card.number
            expiry = f"{card.exp_month:02d} / {card.exp_year % 100:02d}"
            _fill_card_field(page, "number", number)
            _fill_card_field(page, "expiry", expiry)
            _fill_card_field(page, "verification_value", card.cvc)
            _fill_card_field(page, "name", card.name)
            shot(page, "pre-submit")

            # ---- Pay.
            page.locator(
                "button#checkout-pay-button, button:has-text('Pay now'), "
                "button:has-text('Complete order')"
            ).first.click(timeout=15000)

            # ---- Confirmation.
            page.wait_for_url(
                re.compile(r"thank[-_]?you|/orders/|/post_purchase"), timeout=90000
            )
            page.wait_for_load_state("domcontentloaded")
            page.wait_for_timeout(1000)
            shot(page, "confirmation")

            conf_text = page.locator("body").inner_text()
            m = re.search(r"(?:Confirmation|Order)\s*#?\s*([A-Z0-9]+)", conf_text)
            order_ref = m.group(1) if m else page.url

            return CheckoutResult(
                order_reference=order_ref,
                total_usd=total,
                gateway_profile=profile,
                pan_shim=pan_shim,
                screenshots=shots,
            )
        except (CheckoutCeilingViolation, CheckoutError):
            shot(page, "refused")
            raise
        except Exception as exc:
            try:
                shot(page, "failure")
            except Exception:  # noqa: BLE001
                pass
            raise CheckoutError(f"checkout failed: {exc}") from exc
        finally:
            browser.close()


class CheckoutTotalUnreadable(CheckoutError):
    def __init__(self, body_text: str) -> None:
        super().__init__(
            "could not read the order total from the checkout page — refusing "
            "to proceed to card entry"
        )


def _fill_if_present(page, selector: str, value: str) -> None:  # noqa: ANN001
    if not value:
        return
    loc = page.locator(selector).first
    try:
        if loc.count() > 0 and loc.is_visible():
            loc.fill(value)
    except Exception as exc:  # noqa: BLE001
        logger.info("field %s not filled: %s", selector, exc)


def _select_if_present(page, selector: str, value: str) -> None:  # noqa: ANN001
    loc = page.locator(selector).first
    try:
        if loc.count() > 0 and loc.is_visible():
            loc.select_option(value)
    except Exception as exc:  # noqa: BLE001
        logger.info("select %s not set: %s", selector, exc)


def _fill_card_field(page, field: str, value: str) -> None:  # noqa: ANN001
    """Shopify card inputs live in per-field iframes; the inner input's
    id/name/autocomplete carries the field kind across gateway variants."""
    inner_selectors = {
        "number": "input[id='number'], input[name='number'], input[autocomplete='cc-number']",
        "expiry": "input[id='expiry'], input[name='expiry'], input[autocomplete='cc-exp']",
        "verification_value": (
            "input[id='verification_value'], input[name='verification_value'], "
            "input[autocomplete='cc-csc']"
        ),
        "name": "input[id='name'], input[name='name'], input[autocomplete='cc-name']",
    }[field]
    for frame in page.frames:
        try:
            loc = frame.locator(inner_selectors).first
            if loc.count() > 0 and loc.is_visible():
                loc.fill(value)
                return
        except Exception:  # noqa: BLE001
            continue
    # Some gateways render card fields inline (no iframe) — try the page.
    _fill_if_present(page, inner_selectors, value)
