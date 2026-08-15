"""Self-hosted x402 facilitator (WS-x402).

No public facilitator settles XSGD on Avalanche — the hosted ones list USDC
only. So the demo vendor facilitates its own payments: it verifies the buyer's
signed EIP-3009 authorization and submits `transferWithAuthorization` on-chain
itself, via a relayer wallet that pays the gas.

This is a legitimate x402 pattern (the spec calls it self-facilitation), and it
is what makes "x402 + XSGD + Avalanche" possible at all. XSGD is a Circle
FiatToken (FiatTokenV2_2), so it implements EIP-3009 natively — the same scheme
USDC uses — and the x402 library's own exact-EVM facilitator settles it. We
register that facilitator locally with a web3 relayer signer rather than calling
out to an HTTP facilitator.

The relayer holds no user funds. It only relays a transfer the payer already
authorised with their own signature, bounded by the amount in that signature.
"""

from __future__ import annotations

from functools import lru_cache

from core.config import get_settings
from core.logging import get_logger

logger = get_logger(__name__)


def _rpc_url() -> str:
    """RPC for the configured x402 network. Fuji has a dedicated setting; the
    mainnet URL comes from the wallet network table so the two never drift."""
    from services.wallet import _NETWORKS

    s = get_settings()
    if s.x402_network == "eip155:43113":
        return s.avalanche_fuji_rpc_url
    net = _NETWORKS.get(s.x402_network) or {}
    rpc = net.get("rpc")
    if not rpc:
        raise RuntimeError(f"no RPC configured for {s.x402_network}")
    return rpc


@lru_cache
def local_facilitator():
    """A synchronous x402 facilitator that verifies and settles EIP-3009
    payments on-chain via the relayer. Cached — one facilitator per process."""
    from x402.facilitator import x402FacilitatorSync
    from x402.mechanisms.evm.exact import register_exact_evm_facilitator
    from x402.mechanisms.evm.signers import FacilitatorWeb3Signer

    s = get_settings()
    relayer_key = s.x402_relayer_private_key or s.x402_treasury_private_key
    if not relayer_key:
        raise RuntimeError(
            "self-facilitation needs a relayer key — set X402_RELAYER_PRIVATE_KEY "
            "(or X402_TREASURY_PRIVATE_KEY)"
        )
    signer = FacilitatorWeb3Signer(private_key=relayer_key, rpc_url=_rpc_url())
    facilitator = x402FacilitatorSync()
    register_exact_evm_facilitator(facilitator, signer, networks=s.x402_network)
    logger.info(
        "self-facilitation active: relayer %s settles on %s",
        signer.address, s.x402_network,
    )
    return facilitator
