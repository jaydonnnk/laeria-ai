"""Wallet service — the hackathon Funding pillar.

Reads agent/treasury stablecoin + native balances and moves testnet tokens from
the treasury to the agent (the on-stage "funding" moment). Talks raw JSON-RPC
via httpx — no web3 dependency; signing uses eth_account, which the x402 layer
already requires.

The asset is XSGD and the chain is Avalanche. Both remain configurable — the
chain off X402_NETWORK (shared with the payment rail), the token off
STABLECOIN_CONTRACT / _SYMBOL / _DECIMALS — but the defaults now name what
this build actually showcases rather than something it has to be swapped away
from. Nothing in this module assumes any particular token or decimal count;
decimals are read from the contract itself.
"""

from __future__ import annotations

from math import floor

import httpx

from core.config import get_settings
from core.logging import get_logger

logger = get_logger(__name__)

# Per-network constants: RPC, default token, explorer, name.
#
# `token`/`token_symbol`/`token_decimals` are DEFAULTS. STABLECOIN_CONTRACT,
# STABLECOIN_SYMBOL and STABLECOIN_DECIMALS override any of them, which is how
# the funding leg points at XSGD without a code change (see core/config.py).
#
# `testnet` is load-bearing, not documentation: fund_agent refuses to move
# funds on any network where it is False. Adding a mainnet here without that
# flag set correctly is how an env flip becomes a real-money transfer.
_NETWORKS: dict[str, dict] = {
    # The demo chain. XSGD has no public Fuji deployment, so the default token
    # is our own stand-in — same symbol, same 6 decimals as the issued asset,
    # source in infra/contracts/XSGDTest.sol, deployed 2026-08-13. Override
    # with STABLECOIN_CONTRACT to point at a different one.
    "eip155:43113": {
        "name": "Avalanche Fuji (testnet)",
        "chain_id": 43113,
        "rpc": "",  # filled from settings.avalanche_fuji_rpc_url
        "token": "0x4C41454F440a5869cb881895C26dB9d15Ab65cfA",
        "token_symbol": "XSGD",
        "token_decimals": 6,
        "explorer_tx": "https://testnet.snowtrace.io/tx/",
        "native_symbol": "AVAX",
        "testnet": True,
    },
    # Where XSGD actually lives. The address is StraitsX's, VERIFIED on-chain
    # 2026-08-12 via probe_token: symbol "XSGD", 6 decimals. Balances read
    # here are the real asset; `_transfer` still refuses outright because
    # testnet is False, so this is a read-only window by construction and
    # pointing at it cannot move anything.
    "eip155:43114": {
        "name": "Avalanche C-Chain (mainnet)",
        "chain_id": 43114,
        "rpc": "https://api.avax.network/ext/bc/C/rpc",
        "token": "0xb2F85b7AB3c2b6f62DF06dE6aE7D09c010a5096E",
        "token_symbol": "XSGD",
        "token_decimals": 6,
        "explorer_tx": "https://snowtrace.io/tx/",
        "native_symbol": "AVAX",
        "testnet": False,
    },
}

_ERC20_TRANSFER_SELECTOR = "0xa9059cbb"
_ERC20_BALANCEOF_SELECTOR = "0x70a08231"
_ERC20_DECIMALS_SELECTOR = "0x313ce567"
_ERC20_SYMBOL_SELECTOR = "0x95d89b41"


def _decode_abi_string(raw: str) -> str:
    """Decode an ERC-20 string return value.

    Two encodings are in the wild and both must work: the ABI-correct dynamic
    string (offset, length, bytes) and the older fixed `bytes32` that several
    long-lived tokens still return. Guessing one breaks against the other, and
    a token whose symbol cannot be read is exactly the token you most want to
    identify before a demo.
    """
    body = str(raw or "").removeprefix("0x")
    if not body:
        return ""
    if len(body) <= 64:  # bytes32: right-padded with zeros
        return bytes.fromhex(body.ljust(64, "0")).rstrip(b"\x00").decode(
            "utf-8", "replace"
        )
    try:
        length = int(body[64:128], 16)
        return bytes.fromhex(body[128 : 128 + length * 2]).decode("utf-8", "replace")
    except ValueError:
        return ""


class WalletError(RuntimeError):
    pass


def is_testnet(network_id: str) -> bool:
    """Is this CAIP-2 network one we know to be a testnet?

    Fail-closed: a network absent from _NETWORKS reads as NOT a testnet, so an
    unrecognised id is treated as real money and needs the same explicit
    opt-in. The alternative — unknown means safe — is how a typo becomes a
    mainnet signer.
    """
    return bool(_NETWORKS.get(network_id, {}).get("testnet", False))


def configured_token_decimals() -> int:
    """Decimals of the token this deployment is configured for — settings
    first, then the network default, then 6.

    Single source of truth for "how many decimals does our token have",
    deliberately RPC-free so amount-scaling call sites (x402 requirement
    parsing) can use it without a network round trip. `WalletService` refines
    it by asking the contract; nothing anywhere should hardcode 1_000_000
    again, which is what made "swap the funding token to XSGD" a change that
    silently broke every amount outside wallet.py.
    """
    s = get_settings()
    if s.stablecoin_decimals:
        return int(s.stablecoin_decimals)
    net = _NETWORKS.get(s.x402_network) or {}
    return int(net.get("token_decimals") or 6)


class WalletService:
    def __init__(self) -> None:
        settings = get_settings()
        self._settings = settings
        net = _NETWORKS.get(settings.x402_network)
        if net is None:
            raise WalletError(f"unsupported network {settings.x402_network}")
        self._net = dict(net)
        if settings.x402_network == "eip155:43113":
            self._net["rpc"] = settings.avalanche_fuji_rpc_url

        # Env overrides win over the network default, so swapping the funding
        # token is three env vars rather than an edit here.
        if settings.stablecoin_contract:
            self._net["token"] = settings.stablecoin_contract
        if settings.stablecoin_symbol:
            self._net["token_symbol"] = settings.stablecoin_symbol
        if settings.stablecoin_decimals:
            self._net["token_decimals"] = settings.stablecoin_decimals

        if not self._net["token"]:
            raise WalletError(
                f"no token contract for {settings.x402_network} — set "
                "STABLECOIN_CONTRACT and STABLECOIN_SYMBOL"
            )
        self._rpc_url = self._net["rpc"]
        # Resolved lazily from the contract on first use — see _token_decimals.
        self._decimals: int | None = None

    # ---- JSON-RPC plumbing ----

    def _rpc(self, method: str, params: list) -> str | dict:
        resp = httpx.post(
            self._rpc_url,
            json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
            timeout=20,
        )
        resp.raise_for_status()
        body = resp.json()
        if "error" in body:
            raise WalletError(f"{method}: {body['error'].get('message', body['error'])}")
        return body["result"]

    @staticmethod
    def _pad_address(addr: str) -> str:
        return addr.lower().replace("0x", "").rjust(64, "0")

    # ---- token decimals ----

    @property
    def _token_decimals(self) -> int:
        """Decimals for the configured token, asked of the token itself.

        Every amount in this module converts through this, and a wrong value
        misreports balances by orders of magnitude instead of failing — the
        one failure mode STABLECOIN_DECIMALS was always going to produce,
        because it asks a human at 11pm for a number the contract already
        knows. The contract is the authority; config is the fallback for when
        the RPC is unreachable, and a disagreement is logged loudly rather
        than resolved silently.
        """
        if self._decimals is None:
            configured = int(self._net["token_decimals"] or 6)
            try:
                raw = self._rpc(
                    "eth_call",
                    [
                        {"to": self._net["token"], "data": _ERC20_DECIMALS_SELECTOR},
                        "latest",
                    ],
                )
                on_chain = int(str(raw), 16)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "could not read decimals() from %s (%s) — falling back to the "
                    "configured %d",
                    self._net["token"], exc, configured,
                )
                return configured  # not cached: a transient RPC failure must
                                   # not pin a guess for the process lifetime
            # 0 is legal for an ERC-20 but never for a stablecoin, and a
            # nonsense value means we read something that is not a token.
            if not 0 < on_chain <= 36:
                logger.warning(
                    "contract %s reported implausible decimals %d — using the "
                    "configured %d", self._net["token"], on_chain, configured,
                )
                return configured
            if on_chain != configured:
                logger.warning(
                    "token decimals: contract says %d, config says %d — trusting "
                    "the contract", on_chain, configured,
                )
            self._decimals = on_chain
        return self._decimals

    @property
    def _units(self) -> int:
        return 10**self._token_decimals

    def _token_balance(self, addr: str) -> float:
        data = _ERC20_BALANCEOF_SELECTOR + self._pad_address(addr)
        result = self._rpc(
            "eth_call", [{"to": self._net["token"], "data": data}, "latest"]
        )
        return int(str(result), 16) / self._units

    def _native_balance(self, addr: str) -> float:
        result = self._rpc("eth_getBalance", [addr, "latest"])
        return int(str(result), 16) / 10**18

    # ---- public API ----

    def chain_id(self) -> int:
        """Chain id the RPC actually serves. Compared against the configured
        network's expected id — an RPC URL pointing at a different chain than
        X402_NETWORK claims produces balances that look plausible and are
        read from the wrong world."""
        return int(str(self._rpc("eth_chainId", [])), 16)

    def expected_chain_id(self) -> int:
        return int(self._net["chain_id"])

    def probe_token(self) -> dict:
        """Confirm the configured address is a token, and report what it says
        it is. Raises WalletError when it answers neither `symbol()` nor
        `decimals()` — which is what a typo, an EOA, or a wrong-network
        address looks like from here."""
        try:
            raw_symbol = self._rpc(
                "eth_call",
                [{"to": self._net["token"], "data": _ERC20_SYMBOL_SELECTOR}, "latest"],
            )
        except Exception as exc:  # noqa: BLE001
            raise WalletError(
                f"{self._net['token']} did not answer symbol(): {exc}"
            ) from exc
        symbol = _decode_abi_string(str(raw_symbol))
        if not symbol:
            raise WalletError(
                f"{self._net['token']} returned no symbol — is that address a "
                "token on this network?"
            )
        return {
            "address": self._net["token"],
            "symbol": symbol,
            "decimals": self._token_decimals,
            "configured_symbol": self._net["token_symbol"],
        }

    def native_balance(self, addr: str) -> float:
        """Gas balance. Public because gas is a precondition the callers need
        to check, not an implementation detail of a transfer."""
        return self._native_balance(addr)

    def balances(self) -> dict:
        s = self._settings
        out: dict = {
            "network": self._net["name"],
            "network_id": s.x402_network,
            "native_symbol": self._net["native_symbol"],
            "token_contract": self._net["token"],
            "token_symbol": self._net["token_symbol"],
            # Resolved from the contract, not echoed from config — this is the
            # number every balance below was divided by.
            "token_decimals": self._token_decimals,
        }
        for role, addr in (("agent", s.x402_agent_address),
                           ("treasury", s.x402_treasury_address)):
            if not addr:
                out[role] = {"address": "", "error": "address not configured"}
                continue
            try:
                balance = self._token_balance(addr)
                out[role] = {
                    "address": addr,
                    "token": balance,
                    "native": self._native_balance(addr),
                }
            except Exception as exc:  # noqa: BLE001
                out[role] = {"address": addr, "error": str(exc)}
        return out

    # ---- transfers ----

    def _transfer(self, private_key: str, to_address: str, amount: float) -> dict:
        """Sign and broadcast one ERC-20 transfer; return the receipt summary.

        The single place this module moves tokens. Both legs of the demo —
        treasury funding the agent, and the agent settling a purchase — are
        the same operation with different endpoints, and keeping them one
        function means the mainnet refusal below cannot be true of one leg and
        forgotten on the other.

        Refuses on any mainnet: moving real money is a human act, not an
        endpoint. The check reads the network's `testnet` flag rather than a
        hardcoded chain id, so adding a mainnet to _NETWORKS cannot quietly
        open this path.
        """
        from eth_account import Account

        if not self._net.get("testnet"):
            raise WalletError(
                f"refusing to move funds on {self._net['name']} from an API "
                "endpoint — moving mainnet funds is a human act"
            )
        if amount <= 0:
            raise WalletError("amount must be positive")
        if not private_key or not to_address:
            raise WalletError("signing key / destination address not configured")

        units = floor(amount * self._units)
        if units <= 0:
            raise WalletError(
                f"amount {amount} rounds to zero at {self._token_decimals} decimals"
            )
        sender = Account.from_key(private_key)

        nonce = int(str(self._rpc("eth_getTransactionCount", [sender.address, "pending"])), 16)
        gas_price = int(str(self._rpc("eth_gasPrice", [])), 16)
        data = (
            _ERC20_TRANSFER_SELECTOR
            + self._pad_address(to_address)
            + hex(units)[2:].rjust(64, "0")
        )
        tx = {
            "chainId": self._net["chain_id"],
            "nonce": nonce,
            "to": self._net["token"],
            "value": 0,
            "gas": 100_000,
            "gasPrice": max(gas_price, 100_000),  # floor keeps testnet RPCs happy
            "data": data,
        }
        signed = sender.sign_transaction(tx)
        tx_hash = str(self._rpc("eth_sendRawTransaction", [signed.raw_transaction.to_0x_hex()]))
        return {
            "tx_hash": tx_hash,
            "explorer_url": self._net["explorer_tx"] + tx_hash,
            "amount_usd": round(units / self._units, 6),
            "token_symbol": self._net["token_symbol"],
            "from": sender.address,
            "to": to_address,
            "network": self._net["name"],
        }

    def fund_agent(self, amount_usd: float) -> dict:
        """Transfer testnet tokens treasury → agent and return the tx hash."""
        s = self._settings
        if not s.x402_treasury_private_key or not s.x402_agent_address:
            raise WalletError("treasury key / agent address not configured")
        out = self._transfer(
            s.x402_treasury_private_key, s.x402_agent_address, amount_usd
        )
        logger.info(
            "funded agent with %.2f %s: %s",
            out["amount_usd"], out["token_symbol"], out["tx_hash"],
        )
        return out

    def settle_purchase(self, amount: float) -> dict:
        """Transfer `amount` of the stablecoin agent → merchant and return the
        tx hash. The other half of the funding story: the balance that backed
        the card is the balance that pays for the goods, so a purchase leaves
        an on-chain trace rather than only a card authorisation.

        Settles to MERCHANT_SETTLEMENT_ADDRESS, falling back to the treasury —
        which is the demo's merchant, and is who the x402 vendor route is
        already paid at.
        """
        s = self._settings
        merchant = s.merchant_settlement_address or s.x402_treasury_address
        if not merchant:
            raise WalletError(
                "no settlement destination — set MERCHANT_SETTLEMENT_ADDRESS "
                "(or X402_TREASURY_ADDRESS)"
            )
        if not s.x402_agent_private_key:
            raise WalletError("X402_AGENT_PRIVATE_KEY not configured — the agent "
                              "cannot sign its own settlement")
        out = self._transfer(s.x402_agent_private_key, merchant, amount)
        logger.info(
            "settled %.2f %s to merchant %s: %s",
            out["amount_usd"], out["token_symbol"], merchant, out["tx_hash"],
        )
        return out

    def healthcheck(self) -> bool:
        try:
            self._rpc("eth_chainId", [])
            return True
        except Exception as exc:  # noqa: BLE001
            logger.error("wallet healthcheck failed: %s", exc)
            return False
