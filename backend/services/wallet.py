"""Wallet service — the hackathon Funding pillar.

Reads agent/treasury stablecoin + native balances and moves testnet tokens from
the treasury to the agent (the on-stage "funding" moment). Talks raw JSON-RPC
via httpx — no web3 dependency; signing uses eth_account, which the x402 layer
already requires.

Two axes are configurable, and both are event-day swaps:

* Chain, off the same X402_NETWORK the payment rail uses — Base Sepolia →
  Avalanche Fuji is one env flip for both rails.
* Token, off STABLECOIN_CONTRACT / _SYMBOL / _DECIMALS — because the hackathon
  judges funding in XSGD and its address is only obtainable from StraitsX at
  the event. Nothing here assumes USDC or six decimals beyond the defaults.
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
    "eip155:84532": {
        "name": "Base Sepolia (testnet)",
        "chain_id": 84532,
        "rpc": "https://sepolia.base.org",
        "token": "0x036CbD53842c5426634e7929541eC2318f3dCF7e",
        "token_symbol": "USDC",
        "token_decimals": 6,
        "explorer_tx": "https://sepolia.basescan.org/tx/",
        "native_symbol": "ETH",
        "testnet": True,
    },
    "eip155:8453": {
        "name": "Base (mainnet)",
        "chain_id": 8453,
        "rpc": "https://mainnet.base.org",
        "token": "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
        "token_symbol": "USDC",
        "token_decimals": 6,
        "explorer_tx": "https://basescan.org/tx/",
        "native_symbol": "ETH",
        "testnet": False,
    },
    "eip155:43113": {
        "name": "Avalanche Fuji (testnet)",
        "chain_id": 43113,
        "rpc": "",  # filled from settings.avalanche_fuji_rpc_url
        "token": "0x5425890298aed601595a70AB815c96711a31Bc65",
        "token_symbol": "USDC",
        "token_decimals": 6,
        "explorer_tx": "https://testnet.snowtrace.io/tx/",
        "native_symbol": "AVAX",
        "testnet": True,
    },
    # Present only so that a StraitsX sandbox running on Avalanche mainnet is a
    # config flip rather than a code change on the night. Deliberately ships
    # with NO default token: XSGD's address must be pasted in explicitly, so
    # nobody can arrive here by accident and read a balance off some other
    # contract. fund_agent refuses regardless — testnet is False.
    "eip155:43114": {
        "name": "Avalanche C-Chain (mainnet)",
        "chain_id": 43114,
        "rpc": "https://api.avax.network/ext/bc/C/rpc",
        "token": "",
        "token_symbol": "",
        "token_decimals": 6,
        "explorer_tx": "https://snowtrace.io/tx/",
        "native_symbol": "AVAX",
        "testnet": False,
    },
}

_ERC20_TRANSFER_SELECTOR = "0xa9059cbb"
_ERC20_BALANCEOF_SELECTOR = "0x70a08231"


class WalletError(RuntimeError):
    pass


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
                "STABLECOIN_CONTRACT (and STABLECOIN_SYMBOL / "
                "STABLECOIN_DECIMALS if it is not 6-decimal USDC)"
            )
        # Every amount in this module converts through this, so a wrong
        # decimals value misreports balances rather than failing loudly.
        self._units = 10 ** int(self._net["token_decimals"])
        self._rpc_url = self._net["rpc"]

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

    def balances(self) -> dict:
        s = self._settings
        out: dict = {
            "network": self._net["name"],
            "network_id": s.x402_network,
            "native_symbol": self._net["native_symbol"],
            "token_contract": self._net["token"],
            "token_symbol": self._net["token_symbol"],
            "token_decimals": self._net["token_decimals"],
            # Legacy key. The Commerce page reads `usdc_contract`/`usdc`, and
            # renaming those mid-week buys nothing — the values are the same
            # numbers under whichever token is configured.
            "usdc_contract": self._net["token"],
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
                    "usdc": balance,   # legacy key, see above
                    "token": balance,
                    "native": self._native_balance(addr),
                }
            except Exception as exc:  # noqa: BLE001
                out[role] = {"address": addr, "error": str(exc)}
        return out

    def fund_agent(self, amount_usd: float) -> dict:
        """Transfer testnet tokens treasury → agent and return the tx hash.

        Refuses on any mainnet: funding real money is a human act, not an
        endpoint. The check is on the network's `testnet` flag rather than a
        hardcoded chain id, so adding a mainnet to _NETWORKS cannot quietly
        open this path.
        """
        from eth_account import Account

        s = self._settings
        if not self._net.get("testnet"):
            raise WalletError(
                f"refusing to move funds on {self._net['name']} from an API "
                "endpoint — mainnet funding is a human act"
            )
        if amount_usd <= 0:
            raise WalletError("amount must be positive")
        if not s.x402_treasury_private_key or not s.x402_agent_address:
            raise WalletError("treasury key / agent address not configured")

        units = floor(amount_usd * self._units)
        treasury = Account.from_key(s.x402_treasury_private_key)

        nonce = int(str(self._rpc("eth_getTransactionCount", [treasury.address, "pending"])), 16)
        gas_price = int(str(self._rpc("eth_gasPrice", [])), 16)
        data = (
            _ERC20_TRANSFER_SELECTOR
            + self._pad_address(s.x402_agent_address)
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
        signed = treasury.sign_transaction(tx)
        tx_hash = str(self._rpc("eth_sendRawTransaction", [signed.raw_transaction.to_0x_hex()]))
        symbol = self._net["token_symbol"]
        logger.info("funded agent with %.2f %s: %s", amount_usd, symbol, tx_hash)
        return {
            "tx_hash": tx_hash,
            "explorer_url": self._net["explorer_tx"] + tx_hash,
            "amount_usd": round(units / self._units, 6),
            "token_symbol": symbol,
            "from": treasury.address,
            "to": s.x402_agent_address,
            "network": self._net["name"],
        }

    def healthcheck(self) -> bool:
        try:
            self._rpc("eth_chainId", [])
            return True
        except Exception as exc:  # noqa: BLE001
            logger.error("wallet healthcheck failed: %s", exc)
            return False
