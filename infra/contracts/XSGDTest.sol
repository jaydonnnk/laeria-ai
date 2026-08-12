// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/**
 * @title XSGD testnet stand-in
 *
 * Real XSGD is issued by StraitsX on Avalanche C-Chain MAINNET at
 * 0xb2F85b7AB3c2b6f62DF06dE6aE7D09c010a5096E (verified on-chain: symbol
 * "XSGD", 6 decimals). There is no public Fuji deployment, and
 * `WalletService._transfer` refuses to move funds on any mainnet from an API
 * endpoint -- so a testnet demo of the funding and settlement legs needs a
 * token that exists on Fuji.
 *
 * This is that token. Same symbol and same 6 decimals as the real one, so the
 * code path exercised here is the code path that runs against StraitsX's
 * contract: only STABLECOIN_CONTRACT changes.
 *
 * The contract NAME says "test stand-in" deliberately. Anyone reading this on
 * snowtrace should be able to tell in one glance that it is not the issued
 * asset, without having to take anyone's word for it.
 *
 * DEPENDENCY-FREE ON PURPOSE. An OpenZeppelin import means the deploy path
 * needs npm, an import remapping, and a working Remix session. Everything the
 * backend actually calls is transfer / balanceOf / decimals / symbol, so the
 * whole token is forty lines and `scripts/deploy_xsgd.py` can compile it from
 * this single file with no package manager involved.
 *
 * Deploy:  cd backend && python -m scripts.deploy_xsgd
 * Verify:  cd backend && python -m scripts.check_chain
 */
contract XSGDTest {
    string public constant name = "XSGD (Fuji test stand-in)";
    string public constant symbol = "XSGD";
    uint8 public constant decimals = 6;

    uint256 public totalSupply;
    mapping(address => uint256) public balanceOf;
    mapping(address => mapping(address => uint256)) public allowance;

    event Transfer(address indexed from, address indexed to, uint256 value);
    event Approval(address indexed owner, address indexed spender, uint256 value);

    /// @param treasury receives the entire supply — deploy and mint are one
    /// transaction, so there is no separate minting step to forget.
    constructor(address treasury) {
        require(treasury != address(0), "treasury required");
        totalSupply = 1_000_000 * 10 ** uint256(decimals);
        balanceOf[treasury] = totalSupply;
        emit Transfer(address(0), treasury, totalSupply);
    }

    function transfer(address to, uint256 value) external returns (bool) {
        return _transfer(msg.sender, to, value);
    }

    function approve(address spender, uint256 value) external returns (bool) {
        allowance[msg.sender][spender] = value;
        emit Approval(msg.sender, spender, value);
        return true;
    }

    function transferFrom(address from, address to, uint256 value)
        external
        returns (bool)
    {
        uint256 allowed = allowance[from][msg.sender];
        require(allowed >= value, "insufficient allowance");
        // Infinite approval is left untouched, the conventional behaviour.
        if (allowed != type(uint256).max) {
            allowance[from][msg.sender] = allowed - value;
        }
        return _transfer(from, to, value);
    }

    function _transfer(address from, address to, uint256 value)
        private
        returns (bool)
    {
        require(to != address(0), "transfer to zero address");
        uint256 balance = balanceOf[from];
        require(balance >= value, "insufficient balance");
        unchecked {
            // Both safe: the subtraction is guarded above, and the addition
            // cannot overflow because the sum of all balances is totalSupply.
            balanceOf[from] = balance - value;
            balanceOf[to] += value;
        }
        emit Transfer(from, to, value);
        return true;
    }
}
