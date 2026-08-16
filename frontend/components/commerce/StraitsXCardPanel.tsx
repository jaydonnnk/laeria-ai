"use client";

import { useCallback, useEffect, useState } from "react";
import { Card } from "../ui/Card";
import { Button } from "../ui/Button";
import { Badge } from "../ui/Badge";
import { Input, Field } from "../ui/Input";
import { api, Card as CardRow, CardIssueResult } from "../../lib/api";
import { issueCard } from "../../lib/cards";

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

// The card we can currently buy with, resolved from EITHER a just-issued card OR
// the newest persisted StraitsX card row. Persisting it is what makes the buy
// survive a page reload — without it, a refresh would lose the card and force a
// reissue, spending real XSGD again.
interface ActiveCard {
  cardOpaqueId: string;
  settlementTx: string;
  wallet: string;
  amountSgd: number;
  production: boolean;
}

function fromRow(row: CardRow): ActiveCard | null {
  const m = row.metadata ?? {};
  const tx = String(m.settlement_tx ?? "");
  if (!tx) return null;
  return {
    cardOpaqueId: row.issuer_card_id,
    settlementTx: tx,
    wallet: String(m.payer ?? ""),
    amountSgd: row.spend_limit_usd,
    production: m.env === "production",
  };
}

/** Non-custodial StraitsX card issuance + buy. The user signs the card's
 *  EIP-3009 XSGD payment in their own wallet — the key never leaves the browser.
 *  Issued cards persist, so the buy works even after a reload. */
export function StraitsXCardPanel({ refreshKey }: { refreshKey?: number }) {
  const [walletAddress, setWalletAddress] = useState<string | null>(null);
  const [name, setName] = useState("Laeria Agent");
  const [amount, setAmount] = useState("5");
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [active, setActive] = useState<ActiveCard | null>(null);
  const [iframe, setIframe] = useState<string | null>(null);
  const [handle, setHandle] = useState("");
  const [variant, setVariant] = useState("");
  const [order, setOrder] = useState<{ total: number; ref: string; test: boolean } | null>(null);

  const loadWallet = useCallback(async () => {
    try {
      const a = await api.walletAllowance();
      setWalletAddress(!a.custodial && a.address ? a.address : null);
    } catch {
      setWalletAddress(null);
    }
  }, []);

  // Reload-safe: the newest active StraitsX card row becomes the buyable card,
  // so a refresh doesn't lose it. A fresh issuance sets `active` directly, and
  // this keeps it in sync with what's persisted.
  const loadCards = useCallback(async () => {
    try {
      const rows = await api.listCards();
      const straits = rows
        .filter((r) => r.issuer === "straitsx" && r.status !== "canceled")
        .map(fromRow)
        .filter((c): c is ActiveCard => c !== null);
      // Prefer the newest persisted card (carries the correct env for the
      // explorer link); fall back to whatever we hold if none is stored yet.
      setActive((cur) => straits[0] ?? cur);
    } catch {
      /* leave whatever we have */
    }
  }, []);

  useEffect(() => {
    loadWallet();
    loadCards();
  }, [loadWallet, loadCards, refreshKey]);

  async function issue() {
    setErr(null);
    setOrder(null);
    if (!walletAddress) {
      setErr("Connect your wallet above first — it signs and pays for the card.");
      return;
    }
    try {
      const res: CardIssueResult = await issueCard({
        walletAddress,
        cardholderName: name.trim(),
        amountSgd: Number(amount),
        onStep: setBusy,
      });
      setActive({
        cardOpaqueId: res.card_opaque_id,
        settlementTx: res.settlement_tx,
        wallet: walletAddress,
        amountSgd: Number(res.amount_sgd ?? amount),
        // The chain we signed on tells production from sandbox.
        production: false, // refined by loadCards from the persisted env
      });
      setIframe(null);
      await loadCards();
    } catch (e) {
      setErr(errMsg(e));
    } finally {
      setBusy(null);
    }
  }

  async function refreshView() {
    if (!active) return;
    setErr(null);
    try {
      setBusy("Fetching card…");
      const v = await api.cardView(active.cardOpaqueId, active.settlementTx, active.wallet,
        active.production ? "production" : "sandbox");
      setIframe(v.iframe_url ?? null);
    } catch (e) {
      setErr(errMsg(e));
    } finally {
      setBusy(null);
    }
  }

  async function buy() {
    if (!active) return;
    setErr(null);
    setOrder(null);
    if (!handle.trim() || !variant.trim()) {
      setErr("Enter a product handle and variant id (grab them from the shop below).");
      return;
    }
    try {
      setBusy("Buying — driving the merchant checkout…");
      const o = await api.cardCheckout({
        card_opaque_id: active.cardOpaqueId,
        settlement_tx: active.settlementTx,
        wallet_address: active.wallet,
        product_handle: handle.trim(),
        variant_id: variant.trim(),
        card_amount_sgd: active.amountSgd,
        card_env: active.production ? "production" : "sandbox",
      });
      setOrder({ total: o.total_usd, ref: o.order_reference, test: o.pan_shim });
    } catch (e) {
      setErr(errMsg(e));
    } finally {
      setBusy(null);
    }
  }

  const explorer = (tx: string, prod: boolean) =>
    `${prod ? "https://snowtrace.io" : "https://testnet.snowtrace.io"}/tx/${tx}`;

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

      {/* Issue form — always available, so you can mint another card. */}
      <div className="flex items-end gap-3 flex-wrap">
        <Field label="Cardholder name">
          <Input value={name} onChange={(e) => setName(e.target.value)} className="w-[200px]" maxLength={26} />
        </Field>
        <Field label="Amount (SGD)">
          <Input type="number" min={5} max={30} step={1} value={amount} onChange={(e) => setAmount(e.target.value)} className="w-[110px]" />
        </Field>
        <Button onClick={issue} disabled={busy !== null}>
          {busy ?? (active ? "Issue another" : "Issue card")}
        </Button>
      </div>

      {active && (
        <div className="grid gap-3 mt-4 pt-4 border-t border-hairline">
          <div className="flex items-center gap-2 flex-wrap">
            <Badge tone="success" dot>card · {active.amountSgd} SGD</Badge>
            <a className="text-[12px] font-mono text-accent hover:underline"
               href={explorer(active.settlementTx, active.production)} target="_blank" rel="noreferrer">
              settlement tx ↗
            </a>
            <button onClick={refreshView}
              className="text-[12px] font-mono uppercase tracking-wide text-ink-subtle hover:text-ink">
              {busy ?? (iframe ? "refresh card" : "view card")}
            </button>
          </div>
          {iframe ? (
            <iframe src={iframe} title="StraitsX card"
              className="w-full max-w-[380px] h-[230px] rounded-[--radius] border border-hairline bg-white" />
          ) : (
            <p className="text-[13px] text-ink-subtle">
              The view is one-time — hit <b>view card</b> to load it.
            </p>
          )}
          <p className="text-xs text-ink-subtle">
            card id <span className="font-mono">{active.cardOpaqueId}</span>
          </p>

          {/* Buy a real product with this card — the loop's last leg. */}
          <div className="mt-2 pt-3 border-t border-hairline">
            <div className="eyebrow mb-2">Buy a product with this card</div>
            {order ? (
              <div className="grid gap-1 text-[13px]">
                <Badge tone="success" dot>ordered · {order.total.toFixed(2)} · {order.ref}</Badge>
                <p className="text-ink-subtle">
                  Ships to your Profile address.{order.test ? " (bogus gateway — test order)" : ""}
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
