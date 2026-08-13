"""Demo x402-enabled vendor — real protocol, real on-chain settlement.

GET /vendor/deep-report without payment -> 402 with x402 payment
requirements (the configured token on the configured network, payable to the
treasury wallet). Same request with a valid payment header -> facilitator
verifies the signature and settles on-chain, then the content is released
with a receipt header. This is a real x402 seller; the agent's PaymentService
is the real buyer.

Note the facilitator, not this file, decides which assets are settleable on a
given network — on Fuji it lists USDC only. This route is the research
product's paid-API demo and is not one of the four judged milestones, which
run entirely on the card rail and never reach a facilitator.
"""

from __future__ import annotations

from fastapi import APIRouter, Header
from fastapi.responses import JSONResponse

from core.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/vendor", tags=["vendor"])

REPORT_PRICE_USD = 0.01
RESOURCE_PATH = "/vendor/deep-report"


@router.get("/deep-report")
def deep_report(
    payment_signature: str | None = Header(default=None, alias="PAYMENT-SIGNATURE"),
    x_payment: str | None = Header(default=None, alias="X-PAYMENT"),
) -> JSONResponse:
    from services.payment import (
        build_requirements,
        payment_required_body,
        verify_and_settle,
    )

    requirements = build_requirements(REPORT_PRICE_USD, RESOURCE_PATH)
    header_value = payment_signature or x_payment

    if header_value is None:
        return JSONResponse(
            status_code=402,
            content=payment_required_body(requirements, RESOURCE_PATH),
        )

    receipt_header = verify_and_settle(header_value, requirements)
    if receipt_header is None:
        return JSONResponse(
            status_code=402,
            content={
                "error": "payment could not be verified or settled",
                **payment_required_body(requirements, RESOURCE_PATH),
            },
        )

    logger.info("vendor: payment settled on-chain")
    return JSONResponse(
        status_code=200,
        content={
            "title": "Extended Research Report",
            "body": (
                "This content sat behind a real x402 paywall. The agent "
                "detected the 402, signed a payment with its own wallet, the "
                "facilitator verified and settled it on-chain, and the content "
                "unlocked. Production is the same flow on a mainnet."
            ),
            "price_usd": REPORT_PRICE_USD,
        },
        headers={
            "PAYMENT-RESPONSE": receipt_header,
            "X-PAYMENT-RESPONSE": receipt_header,
        },
    )
