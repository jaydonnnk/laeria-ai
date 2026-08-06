"""Disposable virtual-card issuance — the hackathon Issuance pillar.

One abstract `CardIssuer` interface, three implementations:

- `StripeIssuingAdapter` — real test-mode virtual cards via Stripe Issuing.
  Cards carry a per-authorization spending limit (the mandate cap pushed down
  to the card network layer) and are canceled after a single use. Test mode
  exposes the full PAN/CVC via API expand, and test-helpers can simulate a
  matching authorization so the card shows a real transaction even when the
  storefront gateway is simulated (Shopify Bogus Gateway).
- `MockIssuer` — deterministic offline fake (standard 4242 test PAN). Keeps
  the pipeline runnable with zero external deps; also the demo-day fallback
  if venue wifi dies.
- `StraitsXAdapter` — stub to be filled at the hackathon with StraitsX's
  stablecoin-backed card issuance sandbox.

Card PANs/CVCs are NEVER persisted — `get_details` fetches them live from
the issuer on demand; the DB only ever sees last4 + metadata (see
infra/supabase/migrations/002_cards.sql).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone
from math import ceil

from core.config import get_settings
from core.logging import get_logger

logger = get_logger(__name__)


@dataclass
class IssuedCard:
    issuer: str
    issuer_card_id: str
    last4: str
    exp_month: int
    exp_year: int
    spend_limit_usd: float
    status: str  # issued | active | canceled


@dataclass
class CardDetails:
    """Full card credentials — live-fetched, never stored."""

    number: str
    cvc: str
    exp_month: int
    exp_year: int
    brand: str
    name: str


class CardIssuer(ABC):
    name: str

    @abstractmethod
    def issue(
        self, amount_limit_usd: float, merchant_hint: str = "", metadata: dict | None = None
    ) -> IssuedCard: ...

    @abstractmethod
    def get_details(self, issuer_card_id: str) -> CardDetails: ...

    @abstractmethod
    def cancel(self, issuer_card_id: str) -> None: ...

    @abstractmethod
    def list_transactions(self, issuer_card_id: str) -> list[dict]: ...

    @abstractmethod
    def healthcheck(self) -> bool: ...


# ---- Stripe Issuing (test mode) ----

_CARDHOLDER_NAME = "Laeria Agent"
# Cardholder needs a billing address; any plausible one works in test mode.
_TEST_BILLING = {
    "address": {
        "line1": "123 Demo Street",
        "city": "San Francisco",
        "state": "CA",
        "country": "US",
        "postal_code": "94111",
    }
}


class StripeIssuingAdapter(CardIssuer):
    name = "stripe"

    def __init__(self) -> None:
        import stripe

        settings = get_settings()
        if not settings.stripe_api_key.startswith("sk_test_"):
            raise RuntimeError(
                "StripeIssuingAdapter requires a TEST key (sk_test_...) — refusing "
                "to issue real cards from this codepath."
            )
        stripe.api_key = settings.stripe_api_key
        self._stripe = stripe
        self._settings = settings

    def _cardholder_id(self) -> str:
        """Reuse the configured cardholder, or create one on first use.
        (Persist the id in .env STRIPE_CARDHOLDER_ID to skip the lookup.)"""
        if self._settings.stripe_cardholder_id:
            return self._settings.stripe_cardholder_id
        existing = self._stripe.issuing.Cardholder.list(limit=1)
        if existing.data:
            return existing.data[0].id
        ch = self._stripe.issuing.Cardholder.create(
            type="individual",
            name=_CARDHOLDER_NAME,
            email="agent@laeria.local",
            billing=_TEST_BILLING,
        )
        logger.info("created Stripe test cardholder %s — pin it in .env", ch.id)
        return ch.id

    def issue(
        self, amount_limit_usd: float, merchant_hint: str = "", metadata: dict | None = None
    ) -> IssuedCard:
        limit_cents = max(ceil(amount_limit_usd * 100), 1)
        card = self._stripe.issuing.Card.create(
            cardholder=self._cardholder_id(),
            currency="usd",
            type="virtual",
            status="active",
            spending_controls={
                "spending_limits": [
                    {"amount": limit_cents, "interval": "per_authorization"}
                ]
            },
            metadata={"merchant_hint": merchant_hint[:100], **(metadata or {})},
        )
        return IssuedCard(
            issuer=self.name,
            issuer_card_id=card.id,
            last4=card.last4,
            exp_month=card.exp_month,
            exp_year=card.exp_year,
            spend_limit_usd=limit_cents / 100,
            status="active",
        )

    def get_details(self, issuer_card_id: str) -> CardDetails:
        # Full PAN/CVC via expand is a TEST-MODE capability — exactly what we
        # want: real API shape, no real card numbers in the dev loop.
        card = self._stripe.issuing.Card.retrieve(
            issuer_card_id, expand=["number", "cvc"]
        )
        return CardDetails(
            number=card.number,
            cvc=card.cvc,
            exp_month=card.exp_month,
            exp_year=card.exp_year,
            brand=card.brand,
            name=_CARDHOLDER_NAME,
        )

    def cancel(self, issuer_card_id: str) -> None:
        self._stripe.issuing.Card.modify(issuer_card_id, status="canceled")

    def simulate_authorization(self, issuer_card_id: str, amount_usd: float) -> str:
        """Test-helpers: create+capture an authorization on the card so it
        shows a real matching transaction when the storefront gateway is
        simulated (Bogus profile). Returns the authorization id."""
        auth = self._stripe.test_helpers.issuing.Authorization.create(
            card=issuer_card_id,
            amount=max(ceil(amount_usd * 100), 1),
            currency="usd",
        )
        self._stripe.test_helpers.issuing.Authorization.capture(auth.id)
        return auth.id

    def list_transactions(self, issuer_card_id: str) -> list[dict]:
        txns = self._stripe.issuing.Transaction.list(card=issuer_card_id, limit=20)
        return [
            {
                "id": t.id,
                "amount_usd": abs(t.amount) / 100,
                "type": t.type,
                "created": datetime.fromtimestamp(t.created, tz=timezone.utc).isoformat(),
            }
            for t in txns.data
        ]

    def healthcheck(self) -> bool:
        try:
            self._stripe.issuing.Card.list(limit=1)
            return True
        except Exception as exc:  # noqa: BLE001
            logger.error("Stripe Issuing healthcheck failed: %s", exc)
            return False


# ---- Mock issuer (offline fallback) ----

class MockIssuer(CardIssuer):
    """Deterministic fake cards on the standard 4242 test PAN. State lives in
    the cards table like every other issuer; only the credentials are fake."""

    name = "mock"

    def issue(
        self, amount_limit_usd: float, merchant_hint: str = "", metadata: dict | None = None
    ) -> IssuedCard:
        now = datetime.now(timezone.utc)
        return IssuedCard(
            issuer=self.name,
            issuer_card_id=f"mockcard_{int(now.timestamp() * 1000)}",
            last4="4242",
            exp_month=now.month,
            exp_year=now.year + 1,
            spend_limit_usd=round(amount_limit_usd, 2),
            status="active",
        )

    def get_details(self, issuer_card_id: str) -> CardDetails:
        now = datetime.now(timezone.utc)
        return CardDetails(
            number="4242424242424242",
            cvc="424",
            exp_month=now.month,
            exp_year=now.year + 1,
            brand="visa",
            name=_CARDHOLDER_NAME,
        )

    def cancel(self, issuer_card_id: str) -> None:
        return None  # DB row status is the only state a mock card has

    def list_transactions(self, issuer_card_id: str) -> list[dict]:
        return []

    def healthcheck(self) -> bool:
        return True


# ---- StraitsX (hackathon sandbox — fill at the event) ----

def _first(payload: dict, *names: str, default=None):
    """First present value among several candidate key names.

    The one thing genuinely unknown about the StraitsX sandbox until the event
    is what they call their fields — `id` or `card_id` or `cardId`, `last4` or
    `last_four`. Guessing wrong should not mean editing a parser at 11pm, so
    every read below accepts the plausible spellings, including a nested
    `data`/`card` envelope.
    """
    for scope in (payload, payload.get("data") or {}, payload.get("card") or {}):
        if not isinstance(scope, dict):
            continue
        for n in names:
            if scope.get(n) is not None:
                return scope[n]
    return default


class StraitsXAdapter(CardIssuer):
    """Stablecoin-backed virtual cards via StraitsX.

    StraitsX publishes a Card Issuing API with a sandbox, but the credentials
    and the exact contract only arrive at the event. So this is written as a
    real HTTP client against the conventional shape of such an API, with every
    unknown — base URL, auth header, endpoint paths, currency — pushed into
    settings, and every response read tolerantly via `_first`.

    The intent is that going live is filling env vars and, at worst, adjusting
    one path string. Nothing here should need a code change on the night.

    Booth questions this deliberately leaves open:
      - sandbox base URL and auth scheme (bearer? API key header? HMAC?)
      - spend-limit granularity: per-authorization or total
      - is the PAN/CVC retrievable by API in sandbox (we never persist it)
      - terminate vs freeze for single-use disposal
      - XSGD or XUSD funding for a USD-priced merchant
    """

    name = "straitsx"
    _TIMEOUT = 20.0

    def __init__(self) -> None:
        import httpx

        s = get_settings()
        if not s.straitsx_base_url or not s.straitsx_api_key:
            raise RuntimeError(
                "CARD_ISSUER=straitsx needs STRAITSX_BASE_URL and STRAITSX_API_KEY. "
                "Set CARD_ISSUER=mock to run the pipeline without them."
            )
        self._s = s
        self._http = httpx.Client(
            base_url=s.straitsx_base_url.rstrip("/"),
            timeout=self._TIMEOUT,
            headers={
                s.straitsx_auth_header: f"{s.straitsx_auth_prefix}{s.straitsx_api_key}",
                "Content-Type": "application/json",
            },
        )

    def _call(self, method: str, path: str, **kw) -> dict:
        resp = self._http.request(method, path, **kw)
        if resp.status_code >= 400:
            # Surface the body: at an event, the provider's own error message
            # is the fastest route to the right config.
            raise RuntimeError(
                f"StraitsX {method} {path} -> {resp.status_code}: {resp.text[:300]}"
            )
        try:
            return resp.json()
        except ValueError:
            return {}

    def issue(
        self, amount_limit_usd: float, merchant_hint: str = "", metadata: dict | None = None
    ) -> IssuedCard:
        limit = round(max(amount_limit_usd, 0.01), 2)
        body = {
            "type": "virtual",
            "currency": self._s.straitsx_currency,
            "spending_limit": {
                "amount": limit,
                "interval": self._s.straitsx_limit_interval,
            },
            # Sent in both shapes: some APIs take a flat minor-unit amount.
            "limit_amount": limit,
            "metadata": {"merchant_hint": merchant_hint[:100], **(metadata or {})},
        }
        data = self._call("POST", self._s.straitsx_cards_path, json=body)
        card_id = _first(data, "id", "card_id", "cardId", "reference")
        if not card_id:
            raise RuntimeError(f"StraitsX issue returned no card id: {str(data)[:300]}")
        now = datetime.now(timezone.utc)
        return IssuedCard(
            issuer=self.name,
            issuer_card_id=str(card_id),
            last4=str(_first(data, "last4", "last_four", "lastFour", default="")),
            exp_month=int(_first(data, "exp_month", "expiryMonth", "expMonth", default=now.month)),
            exp_year=int(_first(data, "exp_year", "expiryYear", "expYear", default=now.year + 1)),
            spend_limit_usd=limit,
            status="active",
        )

    def get_details(self, issuer_card_id: str) -> CardDetails:
        """Live PAN/CVC. Never persisted — see the module docstring."""
        path = self._s.straitsx_card_secrets_path.format(id=issuer_card_id)
        data = self._call("GET", path)
        number = _first(data, "number", "pan", "card_number", "cardNumber")
        if not number:
            raise RuntimeError(
                f"StraitsX returned no PAN from {path}. If their sandbox does not "
                "expose card credentials by API, the checkout must run under "
                "CHECKOUT_GATEWAY_PROFILE=bogus instead."
            )
        now = datetime.now(timezone.utc)
        return CardDetails(
            number=str(number),
            cvc=str(_first(data, "cvc", "cvv", "securityCode", default="")),
            exp_month=int(_first(data, "exp_month", "expiryMonth", "expMonth", default=now.month)),
            exp_year=int(_first(data, "exp_year", "expiryYear", "expYear", default=now.year + 1)),
            brand=str(_first(data, "brand", "network", "scheme", default="visa")),
            name=_CARDHOLDER_NAME,
        )

    def cancel(self, issuer_card_id: str) -> None:
        """Single-use disposal. Called from a `finally` on every path, so a
        failure here must raise loudly rather than leave a live card behind
        looking cancelled."""
        path = self._s.straitsx_cancel_path.format(id=issuer_card_id)
        method = self._s.straitsx_cancel_method.upper()
        kw = {"json": {"status": "terminated"}} if method in ("PATCH", "PUT", "POST") else {}
        self._call(method, path, **kw)

    def list_transactions(self, issuer_card_id: str) -> list[dict]:
        path = self._s.straitsx_card_txns_path.format(id=issuer_card_id)
        try:
            data = self._call("GET", path)
        except RuntimeError as exc:  # receipts are nice-to-have, not load-bearing
            logger.warning("StraitsX transactions unavailable: %s", exc)
            return []
        rows = data if isinstance(data, list) else (
            _first(data, "transactions", "items", "results", default=[]) or []
        )
        out = []
        for t in rows if isinstance(rows, list) else []:
            if not isinstance(t, dict):
                continue
            out.append(
                {
                    "id": str(_first(t, "id", "transaction_id", default="")),
                    "amount_usd": abs(float(_first(t, "amount", "amount_usd", default=0) or 0)),
                    "type": str(_first(t, "type", "status", default="authorization")),
                    "created": str(
                        _first(t, "created", "created_at", "createdAt", default="")
                    ),
                }
            )
        return out

    def healthcheck(self) -> bool:
        try:
            self._call("GET", self._s.straitsx_cards_path, params={"limit": 1})
            return True
        except Exception as exc:  # noqa: BLE001
            logger.error("StraitsX healthcheck failed: %s", exc)
            return False


def get_issuer() -> CardIssuer:
    kind = get_settings().card_issuer.lower()
    if kind == "stripe":
        return StripeIssuingAdapter()
    if kind == "straitsx":
        return StraitsXAdapter()
    return MockIssuer()
