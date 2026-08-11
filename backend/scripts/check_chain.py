"""Chain preflight — everything the money legs need before they will work.

    python -m scripts.check_chain

`demo_e2e` already asked "does the RPC answer". That is the weakest possible
question: it passes with an empty wallet, a token address that is not a token,
an RPC serving a different chain than the config claims, and a facilitator
that has never heard of the configured network. Every one of those fails later
as something that does not name its own cause — the agent's zero AVAX surfaced
as a settlement error string, three layers from the truth.

Six checks, each phrased as the precondition it actually guards:

  1. RPC serves the chain X402_NETWORK claims
  2. STABLECOIN_CONTRACT is a token and says what it is
  3. On-chain symbol agrees with STABLECOIN_SYMBOL          (warning)
  4. Treasury holds gas                     -> funding transfers
  5. Agent holds gas                        -> settlement transfers
  6. Facilitator supports the network       -> x402 payments  (warning)

Exit 0 = the chain side is ready.
"""

from __future__ import annotations

import sys

# Enough native token for several ERC-20 transfers with room to spare. Fuji
# gas is ~25 nAVAX and a transfer is ~50k gas, so this is generous by design:
# the point is to fail here rather than mid-demo.
MIN_GAS = 0.01


def _fmt(ok: bool | None) -> str:
    return {True: "PASS", False: "FAIL", None: "WARN"}[ok]


def run() -> bool:
    """Returns True when nothing FAILED. Warnings do not block."""
    from core.config import get_settings
    from services.wallet import WalletError, WalletService

    settings = get_settings()
    results: list[bool] = []

    def record(name: str, ok: bool | None, detail: str = "") -> None:
        print(f"  [{_fmt(ok)}] {name}{f': {detail}' if detail else ''}")
        if ok is False:
            results.append(False)

    try:
        wallet = WalletService()
    except WalletError as exc:
        record("wallet configuration", False, str(exc))
        return False

    # 1. Right chain
    try:
        actual, expected = wallet.chain_id(), wallet.expected_chain_id()
        record(
            f"RPC serves chain {expected}",
            actual == expected,
            "" if actual == expected else f"RPC reports {actual} - wrong network",
        )
    except Exception as exc:  # noqa: BLE001
        record(f"RPC reachable ({settings.x402_network})", False, str(exc))
        return False  # nothing below can mean anything

    # 2 + 3. The token is a token, and it is the token we think
    try:
        token = wallet.probe_token()
        record(
            f"token contract responds ({token['address'][:10]}...)",
            True,
            f"{token['symbol']}, {token['decimals']} decimals",
        )
        configured = token["configured_symbol"]
        agrees = (not configured) or configured.upper() == token["symbol"].upper()
        record(
            "on-chain symbol matches STABLECOIN_SYMBOL",
            True if agrees else None,
            "" if agrees else f"chain says {token['symbol']}, config says {configured}",
        )
    except WalletError as exc:
        record("token contract responds", False, str(exc))

    # 4 + 5. Gas, per role, named by what it unblocks
    for role, addr, unblocks in (
        ("treasury", settings.x402_treasury_address, "funding transfers"),
        ("agent", settings.x402_agent_address, "on-chain settlement"),
    ):
        if not addr:
            record(f"{role} gas ({unblocks})", False, "address not configured")
            continue
        try:
            native = wallet.native_balance(addr)
        except Exception as exc:  # noqa: BLE001
            record(f"{role} gas ({unblocks})", False, str(exc))
            continue
        record(
            f"{role} holds gas for {unblocks}",
            native >= MIN_GAS,
            f"{native:.4f} (need >= {MIN_GAS})",
        )

    # 6. Facilitator agrees the network exists. Only the x402 rail needs this,
    #    so it warns rather than blocks the card-rail demo.
    record(*_facilitator_check(settings))

    print()
    ok = all(results) if results else True
    print("CHAIN READY" if ok else "CHAIN NOT READY - fix the FAILs above")
    return ok


def _facilitator_check(settings) -> tuple[str, bool | None, str]:  # noqa: ANN001
    name = f"facilitator supports {settings.x402_network}"
    try:
        import httpx

        url = settings.x402_facilitator_url.rstrip("/") + "/supported"
        data = httpx.get(url, timeout=20).json()
    except Exception as exc:  # noqa: BLE001
        return name, None, f"could not read {settings.x402_facilitator_url}: {exc}"

    items = data.get("kinds") or data.get("supported") or data
    if not isinstance(items, list):
        return name, None, "unrecognised /supported payload"
    networks = {str(i.get("network")) for i in items if isinstance(i, dict)}
    if settings.x402_network in networks:
        return name, True, ""
    return name, None, (
        f"{settings.x402_network} not listed - x402 payments will fail "
        "(card rail is unaffected)"
    )


def main() -> int:
    print("laeria.ai chain preflight\n")
    return 0 if run() else 1


if __name__ == "__main__":
    sys.exit(main())
