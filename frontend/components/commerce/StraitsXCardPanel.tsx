"use client";

import { useCallback, useEffect, useState } from "react";
import { Card } from "../ui/Card";
import { Button } from "../ui/Button";
import { Badge } from "../ui/Badge";
import { Input, Field } from "../ui/Input";
import { api, CardIssueResult, CardCheckoutResult } from "../../lib/api";
import { signTypedData, ensureChain } from "../../lib/wallet";

// The card's EIP-712 domain names a chain; MetaMask refuses to sign a payload
// for a chain it isn't on. Switch to it first. Keyed by the chainId the backend
// puts in typed_data.domain (43113 sandbox / 43114 production).
const CARD_CHAINS: Record<number, { name: string; rpcUrl: string; explorer: string; nativeSymbol: string }> = {
  43113: { name: "Avalanche Fuji", rpcUrl: "https://api.avax-test.network/ext/bc/C/rpc", explorer: "https://testnet.snowtrace.io", nativeSymbol: "AVAX" },
  43114: { name: "Avalanche C-Chain", rpcUrl: "https://api.avax.network/ext/bc/C/rpc", explorer: "https://snowtrace.io", nativeSymbol: "AVAX" },
};

// Wallet (EIP-1193) errors are plain objects {code, message}, not Error
// instances — String() on them yields the useless "[object Object]". Pull the
// message/code out so the panel shows what actually went wrong.
function errMsg(e: unknown): string {
  if (e instanceof Error) return e.message;
  if (e && typeof e === "object") {
    const o = e as { message?: string; code?: number; data?: { message?: string } };
    const parts = [o.message ?? o.data?.message, o.code != null ? `(code ${o.code})` : null].filter(Boolean);
    if (parts.length) return parts.join(" ");
    try { return JSON.stringify(e); } catch { return "unknown error"; }
  }
  return String(e);
}

/** Non-custodial StraitsX card issuance. The user signs the card's EIP-3009
 *  XSGD payment in their own wallet — the key never leaves the browser. The
 *  backend builds the challenge and submits the signed payment (x402). */
export function StraitsXCardPanel({ refreshKey }: { refreshKey?: number }) {
  const [walletAddress, setWalletAddress] = useState<string | null>(null);
  const [name, setName] = useState("Laeria Agent");
  const [amount, setAmount] = useState("5");
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [result, setResult] = useState<CardIssueResult | null>(null);
  const [iframe, setIframe] = useState<string | null>(null);
  // Buy-a-product-with-this-card state.
  const [handle, setHandle] = useState("");
  const [variant, setVariant] = useState("");
  const [order, setOrder] = useState<CardCheckoutResult | null>(null);

  // Read the connected (non-custodial) wallet — the one that signs and pays.
  const loadWallet = useCallback(async () => {
    try {
      const a = await api.walletAllowance();
      setWalletAddress(!a.custodial && a.address ? a.address : null);
    } catch {
      setWalletAddress(null);
    }
  }, []);

  useEffect(() => {
    loadWallet();
  }, [loadWallet, refreshKey]);

  async function issue() {
    setErr(null);
    if (!walletAddress) {
      setErr("Connect your wallet above first — it signs and pays for the card.");
      return;
    }
    try {
      setBusy("Preparing…");
      const challenge = await api.cardChallenge(walletAddress, name.trim(), Number(amount));
      // Switch the wallet to the chain the card's domain names, or MetaMask
      // rejects the signature ("chainId must match the active chainId").
      const chainId = Number(
        (challenge.typed_data as { domain?: { chainId?: number } })?.domain?.chainId
      );
      if (chainId) {
        setBusy(`Switching to ${CARD_CHAINS[chainId]?.name ?? "the card network"}…`);
        await ensureChain(chainId, CARD_CHAINS[chainId]);
      }
      setBusy("Waiting for signature…");
      const sig = await signTypedData(walletAddress, challenge.typed_data);
      setBusy("Settling on-chain…");
      const res = await api.cardIssue(challenge, sig);
      setResult(res);
      // Don't auto-render res.iframe_url: it's a ONE-TIME token, and React's
      // dev StrictMode double-loads the iframe, burning it ("token used"). Let
      // the user click "view card" to fetch a fresh token on demand.
      setIframe(null);
    } catch (e) {
      setErr(errMsg(e));
    } finally {
      setBusy(null);
    }
  }

  async function refreshView() {
    if (!result || !walletAddress) return;
    setErr(null);
    try {
      setBusy("Fetching card…");
      const v = await api.cardView(result.card_opaque_id, result.settlement_tx, walletAddress);
      setIframe(v.iframe_url ?? null);
    } catch (e) {
      setErr(errMsg(e));
    } finally {
      setBusy(null);
    }
  }

  async function buy() {
    if (!result || !walletAddress) return;
    setErr(null);
    setOrder(null);
    if (!handle.trim() || !variant.trim()) {
      setErr("Enter a product handle and variant id (grab them from the shop below).");
      return;
    }
    try {
      setBusy("Buying — driving the merchant checkout…");
      const o = await api.cardCheckout({
        card_opaque_id: result.card_opaque_id,
        settlement_tx: result.settlement_tx,
        wallet_address: walletAddress,
        product_handle: handle.trim(),
        variant_id: variant.trim(),
        card_amount_sgd: Number(result.amount_sgd ?? amount),
      });
      setOrder(o);
    } catch (e) {
      setErr(errMsg(e));
    } finally {
      setBusy(null);
    }
  }

  return (
    <Card className="p-5 mb-4 border-accent/30">
      <div className="flex items-center gap-2 mb-3 flex-wrap">
        <Badge tone="accent" dot>StraitsX Visa</Badge>
        <span className="text-sm font-medium text-ink">Issue a card (self-signed)</span>
      </div>
      <p className="text-sm text-ink-muted mb-4 max-w-[46rem]">
        A real StraitsX virtual Visa, paid for by an XSGD payment on Avalanche via
        x402. <b>You</b> sign the payment in your own wallet — the key never leaves
        your browser. The card is prepaid with the amount you choose (SGD 5–30).
      </p>

      {err && <p className="text-[13px] text-danger mb-3">{err}</p>}

      {!result ? (
        <div className="flex items-end gap-3 flex-wrap">
          <Field label="Cardholder name">
            <Input value={name} onChange={(e) => setName(e.target.value)} className="w-[200px]" maxLength={26} />
          </Field>
          <Field label="Amount (SGD)">
            <Input
              type="number" min={5} max={30} step={1}
              value={amount}
              onChange={(e) => setAmount(e.target.value)}
              className="w-[110px]"
            />
          </Field>
          <Button onClick={issue} disabled={busy !== null}>
            {busy ?? "Issue card"}
          </Button>
        </div>
      ) : (
        <div className="grid gap-3">
          <div className="flex items-center gap-2 flex-wrap">
            <Badge tone="success" dot>issued · {result.amount_sgd} SGD</Badge>
            <a
              className="text-[12px] font-mono text-accent hover:underline"
              href={`https://testnet.snowtrace.io/tx/${result.settlement_tx}`}
              target="_blank" rel="noreferrer"
            >
              settlement tx ↗
            </a>
            <button
              onClick={refreshView}
              className="text-[12px] font-mono uppercase tracking-wide text-ink-subtle hover:text-ink"
            >
              {busy ?? (iframe ? "refresh card" : "view card")}
            </button>
          </div>
          {iframe ? (
            <iframe
              src={iframe}
              title="StraitsX card"
              className="w-full max-w-[380px] h-[230px] rounded-[--radius] border border-hairline bg-white"
            />
          ) : (
            <p className="text-[13px] text-ink-subtle">
              Card issued. The view is one-time — hit <b>view card</b> to load it.
            </p>
          )}
          <p className="text-xs text-ink-subtle">
            card id <span className="font-mono">{result.card_opaque_id}</span>
          </p>

          {/* Buy a real product with this card — the full loop's last leg. */}
          <div className="mt-2 pt-3 border-t border-hairline">
            <div className="eyebrow mb-2">Buy a product with this card</div>
            {order ? (
              <div className="grid gap-1 text-[13px]">
                <Badge tone="success" dot>
                  ordered · {order.total_usd.toFixed(2)} · {order.order_reference}
                </Badge>
                <p className="text-ink-subtle">
                  Ships to your Profile address. {order.pan_shim ? "(bogus gateway — test order)" : ""}
                </p>
              </div>
            ) : (
              <>
                <p className="text-[13px] text-ink-muted mb-2 max-w-[42rem]">
                  Uses this card at the merchant checkout, shipping to your Profile
                  address. Grab the handle + variant id from the shop below.
                </p>
                <div className="flex items-end gap-2 flex-wrap">
                  <Field label="Product handle">
                    <Input value={handle} onChange={(e) => setHandle(e.target.value)} className="w-[200px]" placeholder="the-product-handle" />
                  </Field>
                  <Field label="Variant id">
                    <Input value={variant} onChange={(e) => setVariant(e.target.value)} className="w-[150px]" placeholder="4567890123" />
                  </Field>
                  <Button variant="secondary" onClick={buy} disabled={busy !== null}>
                    {busy ?? "Buy with card"}
                  </Button>
                </div>
              </>
            )}
          </div>
        </div>
      )}
    </Card>
  );
}
