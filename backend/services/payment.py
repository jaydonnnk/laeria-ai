"""Agentic payment execution — x402 + AP2.

Phase 4 only. The vendors you'd actually want to pay (Eventbrite, Amazon,
booking systems) are not x402-enabled, so real purchasing requires either a
demo x402 endpoint on the VPS or mocking the vendor side. The mandate check
is the important real logic — it enforces the co-signed spend rules.
"""

from __future__ import annotations

from core.logging import get_logger
from core.models import Action, ActionMandate

logger = get_logger(__name__)


class PaymentService:
    def verify_within_mandate(
        self, amount_usd: float, category: str, mandate: ActionMandate
    ) -> bool:
        raise NotImplementedError("Phase 4: enforce mandate spend rules")

    def check_402(self, url: str) -> dict | None:
        raise NotImplementedError("Phase 4: detect HTTP 402 Payment Required")

    def pay_402(self, payment_required: dict, mandate: ActionMandate) -> Action:
        raise NotImplementedError("Phase 4: execute x402 USDC payment on Base")
