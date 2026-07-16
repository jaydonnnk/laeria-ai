"""Agentic payment execution — REAL x402 protocol (official Coinbase SDK).

Network: Base Sepolia testnet by default — real EIP-712 signatures, real
facilitator verification and on-chain settlement (https://x402.org/facilitator),
test USDC from https://faucet.circle.com. Production = flip X402_NETWORK to
Base mainnet and fund the agent wallet; no code change.

Buyer side (the agent): x402ClientSync + EthAccountSigner over the agent
wallet. pay_402() detects a 402, builds a signed payment payload, retries
with the payment header, returns content + settlement receipt.

Seller side (our demo vendor route): x402ResourceServerSync + the hosted
facilitator. Builds payment requirements for the 402 response, verifies and
settles incoming payment headers. Vendor code lives in api/routes/vendor.py
and uses the helpers at the bottom of this module.

Mandate enforcement (AP2-style co-signed limits, stored in Supabase) is
rail-agnostic and lives in verify_within_mandate.
"""

from __future__ import annotations

import json
from functools import lru_cache
from urllib.parse import urlparse

import httpx

from core.config import get_settings
from core.logging import get_logger
from core.models import ActionMandate

logger = get_logger(__name__)


class MandateViolation(Exception):
    """Raised when an action falls outside the standing mandate."""


class PaymentService:
    def __init__(self) -> None:
        self._settings = get_settings()
        self._http = httpx.Client(timeout=30.0, follow_redirects=True)

    # ---- mandate enforcement (rail-agnostic, the real safety logic) ----

    def verify_within_mandate(
        self,
        amount_usd: float,
        category: str,
        mandate: ActionMandate,
        spent_this_month: float = 0.0,
        vendor: str = "",
    ) -> tuple[bool, str]:
        """Check an action against the standing rules.

        Returns (needs_confirmation, reason). Raises MandateViolation when the
        action is flat-out not allowed (vs merely needing confirmation).
        """
        if not mandate.autonomous_actions_enabled:
            return True, "autonomous actions are disabled — everything needs confirmation"
        if mandate.max_per_transaction > 0 and amount_usd > mandate.max_per_transaction:
            raise MandateViolation(
                f"amount ${amount_usd:.2f} exceeds per-transaction cap "
                f"${mandate.max_per_transaction:.2f}"
            )
        if mandate.max_per_month > 0 and spent_this_month + amount_usd > mandate.max_per_month:
            raise MandateViolation(
                f"would exceed monthly cap ${mandate.max_per_month:.2f} "
                f"(${spent_this_month:.2f} already spent)"
            )
        if mandate.allowed_categories and category not in mandate.allowed_categories:
            raise MandateViolation(f"category {category!r} is not in allowed_categories")
        if vendor and vendor in mandate.blocked_vendors:
            raise MandateViolation(f"vendor {vendor!r} is blocked")
        if (
            mandate.require_confirmation_above > 0
            and amount_usd > mandate.require_confirmation_above
        ):
            return True, (
                f"${amount_usd:.2f} is above the confirm threshold "
                f"${mandate.require_confirmation_above:.2f}"
            )
        return False, "within mandate — cleared for autonomous execution"

    # ---- x402 buyer side ----

    def _signer_client(self):
        """Lazy x402 client with the agent wallet registered for the
        configured network. Raises if no key configured."""
        from eth_account import Account
        from x402.client import x402ClientSync
        from x402.mechanisms.evm.exact import register_exact_evm_client
        from x402.mechanisms.evm.signers import EthAccountSigner

        key = self._settings.x402_agent_private_key
        if not key:
            raise RuntimeError("X402_AGENT_PRIVATE_KEY not configured in .env")
        account = Account.from_key(key)
        client = x402ClientSync()
        # Register both the configured network and Base mainnet: Bazaar
        # services are mainnet, our demo vendor is testnet. Same wallet signs
        # for both; whether a payment succeeds depends on funding that network.
        networks = {self._settings.x402_network, "eip155:8453"}
        register_exact_evm_client(
            client, EthAccountSigner(account), networks=sorted(networks)
        )
        return client

    def check_402(self, url: str) -> dict | None:
        """GET the URL; if it answers 402, return a summary of the first
        accepted payment option {amount_usd, network, pay_to, raw}."""
        resp = self._http.get(url)
        if resp.status_code != 402:
            return None
        pr = _decode_payment_required(resp)
        if pr is None:
            return None
        req = pr.accepts[0] if getattr(pr, "accepts", None) else None
        if req is None:
            return None
        return {
            "amount_usd": _requirement_amount_usd(req),
            "network": str(getattr(req, "network", "")),
            "pay_to": str(getattr(req, "pay_to", "")),
            "raw": pr,
        }

    def pay_402(self, url: str) -> dict:
        """Full x402 buyer loop: hit URL; on 402 build a signed payment and
        retry. Returns {content, receipt, amount_usd}."""
        from x402.http.constants import (
            PAYMENT_RESPONSE_HEADER,
            PAYMENT_SIGNATURE_HEADER,
            X_PAYMENT_HEADER,
            X_PAYMENT_RESPONSE_HEADER,
        )
        from x402.http.utils import encode_payment_signature_header

        resp = self._http.get(url)
        if resp.status_code != 402:
            resp.raise_for_status()
            return {"content": resp.text, "receipt": None, "amount_usd": 0.0}

        pr = _decode_payment_required(resp)
        if pr is None:
            raise RuntimeError(f"{url} returned 402 without parseable payment requirements")

        req = pr.accepts[0] if getattr(pr, "accepts", None) else None
        amount_usd = _requirement_amount_usd(req) if req else 0.0

        client = self._signer_client()
        payload = client.create_payment_payload(pr)
        header_value = encode_payment_signature_header(payload)

        # v2 uses PAYMENT-SIGNATURE; send the v1 X-PAYMENT alias too for
        # maximum vendor compatibility.
        paid = self._http.get(
            url,
            headers={
                PAYMENT_SIGNATURE_HEADER: header_value,
                X_PAYMENT_HEADER: header_value,
            },
        )
        if paid.status_code == 402:
            # Vendor rejected the payment — surface its stated reason
            # (typically insufficient on-chain balance for the network).
            reason = ""
            try:
                body = paid.json()
                reason = str(
                    body.get("error") or body.get("detail") or body
                )[:200]
            except Exception:  # noqa: BLE001
                reason = paid.text[:200]
            raise RuntimeError(
                f"payment rejected by vendor: {reason} "
                f"(network {req.network if req else '?'} — is the agent "
                "wallet funded there?)"
            )
        paid.raise_for_status()
        receipt = (
            paid.headers.get(PAYMENT_RESPONSE_HEADER)
            or paid.headers.get(X_PAYMENT_RESPONSE_HEADER)
            or ""
        )
        logger.info("paid %s: $%.4f (receipt %s...)", url, amount_usd, receipt[:24])
        return {"content": paid.text, "receipt": receipt, "amount_usd": amount_usd}


# ---- x402 seller side (used by the demo vendor route) ----

@lru_cache
def _resource_server():
    """Singleton resource server wired to the hosted facilitator."""
    from x402.http.facilitator_client import HTTPFacilitatorClientSync
    from x402.mechanisms.evm.exact import register_exact_evm_server
    from x402.server import x402ResourceServerSync

    settings = get_settings()
    facilitator = HTTPFacilitatorClientSync({"url": settings.x402_facilitator_url})
    server = x402ResourceServerSync(facilitator)
    register_exact_evm_server(server, networks=settings.x402_network)
    server.initialize()
    return server


def build_requirements(price_usd: float, resource_path: str):
    """Payment requirements list for a 402 response."""
    from x402.server import ResourceConfig

    settings = get_settings()
    if not settings.x402_treasury_address:
        raise RuntimeError("X402_TREASURY_ADDRESS not configured in .env")
    config = ResourceConfig(
        scheme="exact",
        pay_to=settings.x402_treasury_address,
        price=f"${price_usd}",
        network=settings.x402_network,
        max_timeout_seconds=120,
    )
    return _resource_server().build_payment_requirements(config)


def payment_required_body(requirements, resource_path: str) -> dict:
    """JSON body for the 402 response."""
    from x402.schemas import ResourceInfo

    resource = ResourceInfo(
        url=resource_path,
        description="baryon.ai paid resource",
        service_name="baryon.ai demo vendor",
    )
    pr = _resource_server().create_payment_required_response(requirements, resource)
    return json.loads(pr.model_dump_json(by_alias=True))


def verify_and_settle(header_value: str, requirements) -> str | None:
    """Verify an incoming payment header against our requirements, then
    settle on-chain via the facilitator. Returns an encoded receipt header
    value, or None when verification/settlement fails."""
    from x402.http.utils import (
        decode_payment_signature_header,
        encode_payment_response_header,
    )

    server = _resource_server()
    try:
        payload = decode_payment_signature_header(header_value)
    except Exception as exc:  # noqa: BLE001
        logger.warning("undecodable payment header: %s", exc)
        return None

    matched = server.find_matching_requirements(requirements, payload)
    if matched is None:
        logger.warning("payment payload matches none of our requirements")
        return None
    try:
        verify = server.verify_payment(payload, matched)
        if not getattr(verify, "is_valid", False):
            logger.warning("facilitator rejected payment: %s",
                           getattr(verify, "invalid_reason", "?"))
            return None
        settle = server.settle_payment(payload, matched)
        if not getattr(settle, "success", False):
            logger.warning("settlement failed: %s", getattr(settle, "error_reason", "?"))
            return None
        return encode_payment_response_header(settle)
    except Exception as exc:  # noqa: BLE001
        logger.error("facilitator error: %s", exc)
        return None


def _decode_payment_required(resp: httpx.Response):
    """Parse a 402 response into a PaymentRequired object (v2 header or body)."""
    from x402.http.constants import PAYMENT_REQUIRED_HEADER
    from x402.http.utils import decode_payment_required_header
    from x402.schemas import PaymentRequired

    header = resp.headers.get(PAYMENT_REQUIRED_HEADER)
    if header:
        try:
            return decode_payment_required_header(header)
        except Exception:  # noqa: BLE001
            pass
    try:
        return PaymentRequired.model_validate(resp.json())
    except Exception:  # noqa: BLE001
        return None


def _requirement_amount_usd(req) -> float:
    """Best-effort USD amount from a requirement (USDC has 6 decimals)."""
    raw = getattr(req, "max_amount_required", None) or getattr(req, "amount", None)
    try:
        return float(raw) / 1_000_000
    except (TypeError, ValueError):
        return 0.0


def vendor_host(url: str) -> str:
    return urlparse(url).netloc
