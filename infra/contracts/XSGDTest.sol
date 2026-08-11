// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "@openzeppelin/contracts/token/ERC20/ERC20.sol";

/**
 * @title XSGD testnet stand-in
 *
 * Real XSGD is issued by StraitsX on Avalanche C-Chain MAINNET at
 * 0xb2F85b7AB3c2b6f62DF06dE6aE7D09c010a5096E. There is no public Fuji
 * deployment, and `WalletService._transfer` refuses to move funds on any
 * mainnet from an API endpoint — so a testnet demo of the funding and
 * settlement legs needs a token that exists on Fuji.
 *
 * This is that token. Same symbol and same 6 decimals as the real one, so the
 * code path exercised here is byte-for-byte the code path that runs against
 * StraitsX's contract: only STABLECOIN_CONTRACT changes.
 *
 * The contract NAME says "test stand-in" deliberately. Anyone reading this on
 * snowtrace should be able to tell in one glance that it is not the issued
 * asset, without having to take anyone's word for it.
 *
 * Deploy from the treasury wallet — the constructor mints the whole supply to
 * the address passed in, so deploy and mint are a single transaction.
 *
 *   Network      Avalanche Fuji (chain 43113)
 *   RPC          https://api.avax-test.network/ext/bc/C/rpc
 *   Compiler     0.8.20+
 *   Constructor  treasury = X402_TREASURY_ADDRESS
 *
 * Then, in backend/.env:
 *
 *   X402_NETWORK=eip155:43113
 *   X402_FACILITATOR_URL=https://facilitator.ultravioletadao.xyz
 *   STABLECOIN_CONTRACT=<deployed address>
 *   STABLECOIN_SYMBOL=XSGD
 *   # STABLECOIN_DECIMALS stays blank — read from decimals() below
 *
 * Verify with:  python -m scripts.check_chain
 */
contract XSGDTest is ERC20 {
    uint8 private constant DECIMALS = 6;

    constructor(address treasury) ERC20("XSGD (Fuji test stand-in)", "XSGD") {
        require(treasury != address(0), "treasury required");
        _mint(treasury, 1_000_000 * 10 ** DECIMALS);
    }

    /// @dev Real XSGD uses 6 decimals, not the ERC20 default of 18. The
    /// backend reads this value off the contract rather than trusting config,
    /// so getting it wrong here would misreport every balance downstream.
    function decimals() public pure override returns (uint8) {
        return DECIMALS;
    }
}
