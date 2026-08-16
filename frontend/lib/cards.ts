// Shared StraitsX card flow: build the x402 challenge, sign it in the user's
// wallet, issue the card, and (optionally) buy a product with it. Used by both
// the Commerce panel and the Actions pending-signature fulfilment so the
// non-custodial signing path lives in exactly one place.

import { api, CardCheckoutResult, CardIssueResult } from "./api";
import { signTypedData, ensureChain } from "./wallet";

// The card's EIP-712 domain names a chain; MetaMask refuses to sign for a chain
// it isn't on, so switch first. Keyed by the chainId in typed_data.domain.
export const CARD_CHAINS: Record<number, { name: string; rpcUrl: string; explorer: string; nativeSymbol: string }> = {
  43113: { name: "Avalanche Fuji", rpcUrl: "https://api.avax-test.network/ext/bc/C/rpc", explorer: "https://testnet.snowtrace.io", nativeSymbol: "AVAX" },
  43114: { name: "Avalanche C-Chain", rpcUrl: "https://api.avax.network/ext/bc/C/rpc", explorer: "https://snowtrace.io", nativeSymbol: "AVAX" },
};

/** Challenge -> switch chain -> sign in wallet -> issue the card. Returns the
 *  issued card (opaque id + settlement tx). The signature never leaves the
 *  browser; the backend only builds the challenge and submits it. */
export async function issueCard(opts: {
  walletAddress: string;
  cardholderName: string;
  amountSgd: number;
  onStep?: (s: string) => void;
}): Promise<CardIssueResult> {
  const { walletAddress, cardholderName, amountSgd, onStep } = opts;
  onStep?.("Preparing…");
  const challenge = await api.cardChallenge(walletAddress, cardholderName, amountSgd);
  const chainId = Number((challenge.typed_data as { domain?: { chainId?: number } })?.domain?.chainId);
  if (chainId) {
    onStep?.(`Switching to ${CARD_CHAINS[chainId]?.name ?? "the card network"}…`);
    await ensureChain(chainId, CARD_CHAINS[chainId]);
  }
  onStep?.("Waiting for signature…");
  const sig = await signTypedData(walletAddress, challenge.typed_data);
  onStep?.("Settling on-chain…");
  const issued = await api.cardIssue(challenge, sig);
  const cardEnv = issued.card_env ?? (chainId === 43114 ? "production" : "sandbox");
  return { ...issued, card_env: cardEnv };
}

/** The whole leg for a pending-signature action: issue a card sized to the
 *  purchase, then buy the product with it, recording the receipt on the action. */
export async function issueAndBuy(opts: {
  walletAddress: string;
  cardholderName: string;
  amountSgd: number;
  productHandle: string;
  variantId: string;
  actionId?: string;
  onStep?: (s: string) => void;
}): Promise<CardCheckoutResult> {
  const card = await issueCard(opts);
  opts.onStep?.("Buying — driving the merchant checkout…");
  return api.cardCheckout({
    card_opaque_id: card.card_opaque_id,
    settlement_tx: card.settlement_tx,
    wallet_address: opts.walletAddress,
    product_handle: opts.productHandle,
    variant_id: opts.variantId,
    card_amount_sgd: Number(card.amount_sgd ?? opts.amountSgd),
    action_id: opts.actionId,
    card_env: card.card_env,
    card_html: card.card_html,
  }, (seconds) => opts.onStep?.(`Buying — checkout running ${Math.round(seconds)}s…`));
}
