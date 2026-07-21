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

class StraitsXAdapter(CardIssuer):
    """Stablecoin-backed card issuance via StraitsX.

    To fill in at the hackathon once sandbox credentials exist. Mapping notes
    for their API onto this interface:
      - issue():   create card with a spend limit == the approved mandate
                   amount; their stablecoin-funding leg replaces Stripe's
                   Issuing balance (XUSD/XSGD debit).
      - get_details(): fetch PAN/CVC live; do NOT persist.
      - cancel():  terminate/freeze the card after single use.
      - list_transactions(): card authorizations for the receipt view.
    Questions for the StraitsX booth: sandbox base URL + auth scheme, card
    spend-limit granularity (per-auth vs total), settlement webhook shape,
    XSGD vs XUSD funding for USD-priced merchants.
    """

    name = "straitsx"

    def issue(self, amount_limit_usd, merchant_hint="", metadata=None):  # noqa: ANN001
        raise NotImplementedError("StraitsX sandbox access is granted at the event")

    def get_details(self, issuer_card_id):  # noqa: ANN001
        raise NotImplementedError

    def cancel(self, issuer_card_id):  # noqa: ANN001
        raise NotImplementedError

    def list_transactions(self, issuer_card_id):  # noqa: ANN001
        raise NotImplementedError

    def healthcheck(self) -> bool:
        return False


def get_issuer() -> CardIssuer:
    kind = get_settings().card_issuer.lower()
    if kind == "stripe":
        return StripeIssuingAdapter()
    if kind == "straitsx":
        return StraitsXAdapter()
    return MockIssuer()
