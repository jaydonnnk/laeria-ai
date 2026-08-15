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
 * needs npm, an import remapping, and a working Remix session, so the whole
 * token compiles from this single file with no package manager involved.
 *
 * EIP-3009 (`transferWithAuthorization`) is implemented here because the real
 * XSGD is a Circle FiatToken and DOES support it, and the x402 rail settles
 * through exactly that function. Without it, a Fuji test of the x402 leg would
 * pass against a token that behaves unlike the mainnet asset. The EIP-712
 * domain is {name: "XSGD", version: "2"} — the FiatToken shape — so a signature
 * produced for the mainnet token verifies here too (only chainId + address,
 * which travel with the network, differ).
 *
 * Deploy:  cd backend && python -m scripts.deploy_xsgd
 * Verify:  cd backend && python -m scripts.check_chain
 */
contract XSGDTest {
    string public constant name = "XSGD (Fuji test stand-in)";
    string public constant symbol = "XSGD";
    uint8 public constant decimals = 6;

    // EIP-712 domain for EIP-3009 signatures. NOT the ERC-20 name above: it is
    // the FiatToken domain name so mainnet-shaped signatures verify.
    string private constant EIP712_NAME = "XSGD";
    string private constant EIP712_VERSION = "2";

    bytes32 private constant TRANSFER_WITH_AUTHORIZATION_TYPEHASH = keccak256(
        "TransferWithAuthorization(address from,address to,uint256 value,uint256 validAfter,uint256 validBefore,bytes32 nonce)"
    );

    uint256 public totalSupply;
    mapping(address => uint256) public balanceOf;
    mapping(address => mapping(address => uint256)) public allowance;
    // authorizer => nonce => used. A nonce is single-use, per EIP-3009.
    mapping(address => mapping(bytes32 => bool)) public authorizationState;

    event Transfer(address indexed from, address indexed to, uint256 value);
    event Approval(address indexed owner, address indexed spender, uint256 value);
    event AuthorizationUsed(address indexed authorizer, bytes32 indexed nonce);

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

    // ---- EIP-3009: transfer with an off-chain signed authorization ----

    /// Recomputed each call (not cached) so a chain fork can't invalidate it —
    /// the FiatTokenV2_2 behaviour.
    function DOMAIN_SEPARATOR() public view returns (bytes32) {
        return keccak256(
            abi.encode(
                keccak256(
                    "EIP712Domain(string name,string version,uint256 chainId,address verifyingContract)"
                ),
                keccak256(bytes(EIP712_NAME)),
                keccak256(bytes(EIP712_VERSION)),
                block.chainid,
                address(this)
            )
        );
    }

    /// (v, r, s) overload.
    function transferWithAuthorization(
        address from,
        address to,
        uint256 value,
        uint256 validAfter,
        uint256 validBefore,
        bytes32 nonce,
        uint8 v,
        bytes32 r,
        bytes32 s
    ) external {
        _transferWithAuthorization(from, to, value, validAfter, validBefore, nonce, v, r, s);
    }

    /// Packed-signature overload — the x402 facilitator may call either.
    function transferWithAuthorization(
        address from,
        address to,
        uint256 value,
        uint256 validAfter,
        uint256 validBefore,
        bytes32 nonce,
        bytes memory signature
    ) external {
        require(signature.length == 65, "bad signature length");
        bytes32 r;
        bytes32 s;
        uint8 v;
        assembly {
            r := mload(add(signature, 0x20))
            s := mload(add(signature, 0x40))
            v := byte(0, mload(add(signature, 0x60)))
        }
        _transferWithAuthorization(from, to, value, validAfter, validBefore, nonce, v, r, s);
    }

    function _transferWithAuthorization(
        address from,
        address to,
        uint256 value,
        uint256 validAfter,
        uint256 validBefore,
        bytes32 nonce,
        uint8 v,
        bytes32 r,
        bytes32 s
    ) private {
        require(block.timestamp > validAfter, "authorization not yet valid");
        require(block.timestamp < validBefore, "authorization expired");
        require(!authorizationState[from][nonce], "authorization already used");

        bytes32 structHash = keccak256(
            abi.encode(
                TRANSFER_WITH_AUTHORIZATION_TYPEHASH,
                from, to, value, validAfter, validBefore, nonce
            )
        );
        bytes32 digest = keccak256(
            abi.encodePacked("\x19\x01", DOMAIN_SEPARATOR(), structHash)
        );
        address signer = ecrecover(digest, v, r, s);
        require(signer != address(0) && signer == from, "invalid signature");

        authorizationState[from][nonce] = true;
        emit AuthorizationUsed(from, nonce);
        _transfer(from, to, value);
    }
}
