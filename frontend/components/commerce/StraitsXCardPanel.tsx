"use client";

import { useCallback, useEffect, useState } from "react";
import { Card } from "../ui/Card";
import { Button } from "../ui/Button";
import { Badge } from "../ui/Badge";
import { Input, Field } from "../ui/Input";
import { api, CardIssueResult } from "../../lib/api";
import { signTypedData } from "../../lib/wallet";

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
      setBusy("Waiting for signature…");
      const sig = await signTypedData(walletAddress, challenge.typed_data);
      setBusy("Settling on-chain…");
      const res = await api.cardIssue(challenge, sig);
      setResult(res);
      setIframe(res.iframe_url ?? null);
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
              {busy ?? "refresh card"}
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
              The card view is one-time — hit <b>refresh card</b> to load it again.
            </p>
          )}
          <p className="text-xs text-ink-subtle">
            card id <span className="font-mono">{result.card_opaque_id}</span>
          </p>
        </div>
      )}
    </Card>
  );
}
