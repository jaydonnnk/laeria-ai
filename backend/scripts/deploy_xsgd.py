"""Deploy the Fuji XSGD stand-in — no browser, no wallet extension.

    python -m scripts.deploy_xsgd            # deploy
    python -m scripts.deploy_xsgd --dry-run  # compile + estimate only

Remix is the documented path and it is a bad one: a browser IDE, a wallet
extension, an injected-provider handshake and a network selector all have to
agree before a single byte is compiled, and each of them can fail in a way
that looks like one of the others. The treasury key is already in .env and
web3 is already a dependency, so the whole thing is one command.

Signs with the SAME treasury key the funding leg uses, so the deployer is the
address that ends up holding the supply, and no key leaves the machine.

REFUSES ANY MAINNET. Deployment is cheap and reversible on a testnet and
neither on a real chain; the guard mirrors WalletService._transfer rather than
trusting whoever set X402_NETWORK.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

CONTRACT = Path(__file__).resolve().parents[2] / "infra" / "contracts" / "XSGDTest.sol"
SOLC_VERSION = "0.8.20"

# Chains this script will deploy to. Fuji is the one we use; Base Sepolia is
# here so the fallback plan needs no edit. Anything absent is refused, which
# is the point -- a mainnet chain id must never be reachable by typo.
_TESTNETS = {43113: "Avalanche Fuji", 84532: "Base Sepolia"}


def _compile() -> tuple[str, list]:
    """Compile the contract, installing solc on first run. Returns
    (bytecode, abi)."""
    try:
        import solcx
    except ImportError:
        raise SystemExit(
            "py-solc-x is not installed. It is a deploy-only dependency and is "
            "deliberately kept out of requirements.txt so the service image "
            "does not carry a Solidity compiler:\n\n"
            "    .venv/Scripts/python -m pip install py-solc-x\n"
        ) from None

    try:
        solcx.set_solc_version(SOLC_VERSION)
    except Exception:  # noqa: BLE001 — not installed yet
        print(f"  installing solc {SOLC_VERSION} (one time)...")
        solcx.install_solc(SOLC_VERSION)
        solcx.set_solc_version(SOLC_VERSION)

    compiled = solcx.compile_files(
        [str(CONTRACT)], output_values=["abi", "bin"], optimize=True
    )
    key = next(k for k in compiled if k.endswith(":XSGDTest"))
    iface = compiled[key]
    print(f"  compiled {CONTRACT.name} ({len(iface['bin']) // 2} bytes)")
    return iface["bin"], iface["abi"]


def main() -> int:
    parser = argparse.ArgumentParser(description="deploy the XSGD test token")
    parser.add_argument(
        "--dry-run", action="store_true", help="compile and estimate gas, do not send"
    )
    args = parser.parse_args()

    from eth_account import Account
    from web3 import Web3

    from core.config import get_settings
    from services.wallet import _NETWORKS  # noqa: PLC2701 — same package

    settings = get_settings()
    print("laeria.ai — deploy XSGD test token\n")

    net = _NETWORKS.get(settings.x402_network)
    if net is None:
        print(f"  [FAIL] unsupported X402_NETWORK {settings.x402_network}")
        return 1
    rpc = (
        settings.avalanche_fuji_rpc_url
        if settings.x402_network == "eip155:43113"
        else net["rpc"]
    )

    w3 = Web3(Web3.HTTPProvider(rpc, request_kwargs={"timeout": 30}))
    if not w3.is_connected():
        print(f"  [FAIL] cannot reach RPC {rpc}")
        return 1

    chain_id = w3.eth.chain_id
    if chain_id not in _TESTNETS:
        print(
            f"  [FAIL] chain {chain_id} is not a testnet this script will deploy "
            f"to. Allowed: {sorted(_TESTNETS)}. Deploying a token from a script "
            "is a testnet act."
        )
        return 1
    print(f"  network   {_TESTNETS[chain_id]} (chain {chain_id})")

    key = settings.x402_treasury_private_key
    if not key:
        print("  [FAIL] X402_TREASURY_PRIVATE_KEY not set in .env")
        return 1
    treasury = Account.from_key(key)
    configured = settings.x402_treasury_address
    if configured and treasury.address.lower() != configured.lower():
        print(
            f"  [FAIL] the treasury KEY derives {treasury.address} but "
            f"X402_TREASURY_ADDRESS is {configured} — refusing to mint a supply "
            "to an address that is not the one the backend reads"
        )
        return 1

    balance = w3.eth.get_balance(treasury.address)
    print(f"  deployer  {treasury.address}")
    print(f"  balance   {w3.from_wei(balance, 'ether')} native")
    if balance == 0:
        # The likely cause is not an unfunded wallet but the wrong chain: the
        # network comes from .env, and it has to be flipped BEFORE deploying
        # rather than after, since the whole point of the deploy is to produce
        # the token that .env will then point at.
        print(
            f"  [FAIL] deployer has no gas on {_TESTNETS[chain_id]}.\n"
            f"         X402_NETWORK in .env selects the chain and is currently "
            f"{settings.x402_network}.\n"
            f"         For the Fuji deploy set X402_NETWORK=eip155:43113 first, "
            "then re-run."
        )
        return 1

    bytecode, abi = _compile()

    contract = w3.eth.contract(abi=abi, bytecode=bytecode)

    # Estimate and price the transaction explicitly. Neither value can be left
    # to web3 here:
    #
    # * `gas` — when the gas price is near zero (Fuji's base fee was 10 wei
    #   when this was written) build_transaction fills the limit with a
    #   balance-derived figure instead of the estimate. Observed: 3.1e15
    #   against a real estimate of 420,480, which is eight orders of magnitude
    #   past the 32M block limit, so the node rejects the transaction outright.
    # * `gasPrice` — a literally-current price of 160 wei is accepted now and
    #   stuck the moment the next block prices higher. The floor costs a
    #   fraction of a cent on a testnet and buys inclusion.
    estimated = contract.constructor(treasury.address).estimate_gas(
        {"from": treasury.address}
    )
    block_limit = w3.eth.get_block("latest")["gasLimit"]
    gas = min(int(estimated * 1.25), block_limit)
    gas_price = max(w3.eth.gas_price, w3.to_wei(1, "gwei"))

    tx = contract.constructor(treasury.address).build_transaction(
        {
            "from": treasury.address,
            "nonce": w3.eth.get_transaction_count(treasury.address, "pending"),
            "chainId": chain_id,
            "gas": gas,
            "gasPrice": gas_price,
        }
    )
    cost = w3.from_wei(gas * gas_price, "ether")
    print(
        f"  gas       {gas:,} (estimate {estimated:,}) @ "
        f"{w3.from_wei(gas_price, 'gwei')} gwei — max cost {cost} native"
    )
    if gas * gas_price > balance:
        print("  [FAIL] deployer cannot cover the maximum gas cost")
        return 1

    if args.dry_run:
        print("\n  --dry-run: compiled and estimated, nothing sent.")
        return 0

    signed = treasury.sign_transaction(tx)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    print(f"\n  sent      {tx_hash.hex()}")
    print("  waiting for the receipt...")
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=180)

    if receipt.status != 1:
        print("  [FAIL] the deployment transaction reverted")
        return 1

    address = receipt.contractAddress
    explorer = net["explorer_tx"].replace("/tx/", "/address/") + address

    # Read it back off the chain. Deploying is not the claim -- the claim is
    # that the backend can read this token, so ask it the same questions
    # check_chain will.
    token = w3.eth.contract(address=address, abi=abi)
    symbol = token.functions.symbol().call()
    decimals = token.functions.decimals().call()
    supply = token.functions.balanceOf(treasury.address).call()

    print(f"\n  DEPLOYED  {address}")
    print(f"  verified  symbol={symbol} decimals={decimals} "
          f"treasury balance={supply / 10 ** decimals:,.2f}")
    print(f"  explorer  {explorer}")
    print("\nAdd to backend/.env (leave STABLECOIN_DECIMALS blank):\n")
    print(f"  X402_NETWORK={settings.x402_network}")
    print("  X402_FACILITATOR_URL=https://facilitator.ultravioletadao.xyz")
    print(f"  STABLECOIN_CONTRACT={address}")
    print(f"  STABLECOIN_SYMBOL={symbol}")
    print("\nThen: python -m scripts.check_chain")
    return 0


if __name__ == "__main__":
    sys.exit(main())
