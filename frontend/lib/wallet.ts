// Minimal EIP-1193 wallet helpers — no web3 dependency. Talks to whatever
// injected provider (MetaMask, Rabby, …) exposes window.ethereum, so the app
// stays free of wagmi/viem/ethers and their bundle.

interface Eip1193Provider {
  request(args: { method: string; params?: unknown[] }): Promise<unknown>;
  on?(event: string, handler: (...args: unknown[]) => void): void;
  removeListener?(event: string, handler: (...args: unknown[]) => void): void;
}

declare global {
  interface Window {
    ethereum?: Eip1193Provider;
  }
}

export function getEthereum(): Eip1193Provider | null {
  if (typeof window === "undefined") return null;
  return window.ethereum ?? null;
}

export function hasWallet(): boolean {
  return getEthereum() !== null;
}

/** Prompt the user to connect and return the selected address. */
export async function connectWallet(): Promise<string> {
  const eth = getEthereum();
  if (!eth) throw new Error("No wallet found — install MetaMask or Rabby.");
  const accounts = (await eth.request({ method: "eth_requestAccounts" })) as string[];
  if (!accounts?.length) throw new Error("No account authorised.");
  return accounts[0];
}

/** Switch to the given chain, adding it first if the wallet doesn't have it. */
export async function ensureChain(chainIdDec: number, opts?: {
  name: string;
  rpcUrl: string;
  explorer: string;
  nativeSymbol: string;
}): Promise<void> {
  const eth = getEthereum();
  if (!eth) throw new Error("No wallet found.");
  const chainIdHex = "0x" + chainIdDec.toString(16);
  try {
    await eth.request({
      method: "wallet_switchEthereumChain",
      params: [{ chainId: chainIdHex }],
    });
  } catch (err) {
    // 4902 = chain not added yet. Add it, then it becomes current.
    const code = (err as { code?: number })?.code;
    if (code === 4902 && opts) {
      await eth.request({
        method: "wallet_addEthereumChain",
        params: [
          {
            chainId: chainIdHex,
            chainName: opts.name,
            rpcUrls: [opts.rpcUrl],
            blockExplorerUrls: [opts.explorer],
            nativeCurrency: { name: opts.nativeSymbol, symbol: opts.nativeSymbol, decimals: 18 },
          },
        ],
      });
    } else {
      throw err;
    }
  }
}

const APPROVE_SELECTOR = "0x095ea7b3"; // approve(address spender, uint256 amount)

function padWord(hexNo0x: string): string {
  return hexNo0x.toLowerCase().replace(/^0x/, "").padStart(64, "0");
}

/** Convert a human token amount to base units for the given decimals, exactly
 *  (no float drift): scale as a string, not via Number * 10**decimals. */
export function toUnits(amount: string | number, decimals: number): bigint {
  const s = String(amount).trim();
  const [whole, frac = ""] = s.split(".");
  const fracPadded = (frac + "0".repeat(decimals)).slice(0, decimals);
  return BigInt((whole || "0") + fracPadded);
}

/** Send an ERC-20 approve(operator, amount). Returns the tx hash. */
export async function approveSpending(params: {
  from: string;
  token: string;
  operator: string;
  amountUnits: bigint;
}): Promise<string> {
  const eth = getEthereum();
  if (!eth) throw new Error("No wallet found.");
  const data =
    APPROVE_SELECTOR +
    padWord(params.operator) +
    padWord(params.amountUnits.toString(16));
  const txHash = (await eth.request({
    method: "eth_sendTransaction",
    params: [{ from: params.from, to: params.token, data }],
  })) as string;
  return txHash;
}
