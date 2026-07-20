"""Wallet service — the hackathon Funding pillar.

Reads agent/treasury USDC + native balances and moves testnet USDC from the
treasury to the agent (the on-stage "funding" moment). Talks raw JSON-RPC via
httpx — no web3 dependency; signing uses eth_account, which the x402 layer
already requires.

Network-aware off the same X402_NETWORK setting the payment rail uses, so a
chain swap (Base Sepolia → Avalanche Fuji at the hackathon) is one env flip
for both rails. The fiat/KYC on-ramp story (StraitsX XUSD/XSGD) narrates over
this at the event; the live mechanics are the on-chain transfer below.
"""

from __future__ import annotations

from math import floor

import httpx

from core.config import get_settings
from core.logging import get_logger

logger = get_logger(__name__)

# Per-network constants: RPC, USDC contract (6 decimals), explorer, name.
_NETWORKS: dict[str, dict] = {
    "eip155:84532": {
        "name": "Base Sepolia (testnet)",
        "chain_id": 84532,
        "rpc": "https://sepolia.base.org",
        "usdc": "0x036CbD53842c5426634e7929541eC2318f3dCF7e",
        "explorer_tx": "https://sepolia.basescan.org/tx/",
        "native_symbol": "ETH",
    },
    "eip155:8453": {
        "name": "Base (mainnet)",
        "chain_id": 8453,
        "rpc": "https://mainnet.base.org",
        "usdc": "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
        "explorer_tx": "https://basescan.org/tx/",
        "native_symbol": "ETH",
    },
    "eip155:43113": {
        "name": "Avalanche Fuji (testnet)",
        "chain_id": 43113,
        "rpc": "",  # filled from settings.avalanche_fuji_rpc_url
        "usdc": "0x5425890298aed601595a70AB815c96711a31Bc65",
        "explorer_tx": "https://testnet.snowtrace.io/tx/",
        "native_symbol": "AVAX",
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

    def _usdc_balance(self, addr: str) -> float:
        data = _ERC20_BALANCEOF_SELECTOR + self._pad_address(addr)
        result = self._rpc(
            "eth_call", [{"to": self._net["usdc"], "data": data}, "latest"]
        )
        return int(str(result), 16) / 1_000_000

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
            "usdc_contract": self._net["usdc"],
        }
        for role, addr in (("agent", s.x402_agent_address),
                           ("treasury", s.x402_treasury_address)):
            if not addr:
                out[role] = {"address": "", "error": "address not configured"}
                continue
            try:
                out[role] = {
                    "address": addr,
                    "usdc": self._usdc_balance(addr),
                    "native": self._native_balance(addr),
                }
            except Exception as exc:  # noqa: BLE001
                out[role] = {"address": addr, "error": str(exc)}
        return out

    def fund_agent(self, amount_usd: float) -> dict:
        """Transfer testnet USDC treasury → agent and return the tx hash.
        Refuses on mainnet: funding real money is a human act, not an
        endpoint."""
        from eth_account import Account

        s = self._settings
        if s.x402_network == "eip155:8453":
            raise WalletError("refusing to move mainnet funds from an API endpoint")
        if amount_usd <= 0:
            raise WalletError("amount must be positive")
        if not s.x402_treasury_private_key or not s.x402_agent_address:
            raise WalletError("treasury key / agent address not configured")

        units = floor(amount_usd * 1_000_000)
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
            "to": self._net["usdc"],
            "value": 0,
            "gas": 100_000,
            "gasPrice": max(gas_price, 100_000),  # floor keeps testnet RPCs happy
            "data": data,
        }
        signed = treasury.sign_transaction(tx)
        tx_hash = str(self._rpc("eth_sendRawTransaction", [signed.raw_transaction.to_0x_hex()]))
        logger.info("funded agent with %.2f USDC: %s", amount_usd, tx_hash)
        return {
            "tx_hash": tx_hash,
            "explorer_url": self._net["explorer_tx"] + tx_hash,
            "amount_usd": round(units / 1_000_000, 6),
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
