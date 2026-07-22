"use client";

import { useState } from "react";
import { api, Card as CardT, CardDetails, CardTransaction } from "../lib/api";
import { Badge } from "./ui/Badge";
import { Button } from "./ui/Button";
import { cn } from "../lib/cn";

// A disposable virtual card. Credentials are never in the row data —
// "Reveal" live-fetches PAN/CVC and holds them in component state only.

export default function CardView({
  card,
  onChanged,
  onError,
}: {
  card: CardT;
  onChanged: () => Promise<void>;
  onError: (e: string) => void;
}) {
  const [details, setDetails] = useState<CardDetails | null>(null);
  const [txns, setTxns] = useState<CardTransaction[] | null>(null);
  const [busy, setBusy] = useState(false);
  const canceled = card.status === "canceled";

  async function run(fn: () => Promise<void>) {
    setBusy(true);
    try {
      await fn();
    } catch (err) {
      onError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  const number = details
    ? details.number.replace(/(.{4})/g, "$1 ").trim()
    : `•••• •••• •••• ${card.last4}`;

  return (
    <div>
      {/* card face */}
      <div
        className={cn(
          "relative rounded-[--radius-lg] p-5 text-white overflow-hidden transition-opacity",
          canceled ? "opacity-60" : "opacity-100"
        )}
        style={{
          background: canceled
            ? "linear-gradient(135deg,#6e7781,#565d66)"
            : "linear-gradient(135deg,#1f2328 0%,#0b7a5b 180%)",
        }}
      >
        {/* guilloché sheen */}
        <div
          className="absolute inset-0 opacity-[0.14] pointer-events-none"
          style={{
            background:
              "repeating-linear-gradient(115deg, transparent 0 8px, rgba(255,255,255,0.5) 8px 9px)",
          }}
        />
        <div className="relative flex items-center justify-between text-[11px] font-mono uppercase tracking-wider">
          <span className="opacity-80">{card.issuer} · disposable</span>
          <span className="text-[#7ee787]">
            limit ${Number(card.spend_limit_usd).toFixed(2)}
          </span>
        </div>
        <div className="relative tnum text-[19px] tracking-[0.06em] mt-6 mb-4">
          {number}
        </div>
        <div className="relative flex items-center justify-between text-[12px] font-mono">
          <span className="opacity-90">{details ? details.name : "LAERIA AGENT"}</span>
          <span className="opacity-90">
            {String(card.exp_month).padStart(2, "0")}/{card.exp_year % 100}
            {details && <span> · CVC {details.cvc}</span>}
          </span>
        </div>
      </div>

      {/* controls */}
      <div className="flex items-center flex-wrap gap-2 mt-3">
        <Badge status={card.status}>{card.status}</Badge>
        {!canceled && (
          <>
            <Button
              variant="secondary"
              size="sm"
              disabled={busy || !!details}
              onClick={() => run(async () => setDetails(await api.cardDetails(card.id)))}
            >
              {details ? "Revealed" : "Reveal"}
            </Button>
            <Button
              variant="danger"
              size="sm"
              disabled={busy}
              onClick={() =>
                run(async () => {
                  await api.cancelCard(card.id);
                  setDetails(null);
                  await onChanged();
                })
              }
            >
              Cancel card
            </Button>
          </>
        )}
        <Button
          variant="ghost"
          size="sm"
          disabled={busy}
          onClick={() => run(async () => setTxns(await api.cardTransactions(card.id)))}
        >
          Transactions
        </Button>
        <span className="tnum text-xs text-ink-subtle ml-auto">
          {new Date(card.created_at).toLocaleDateString()}
        </span>
      </div>

      {txns && (
        <div className="mt-2 text-[13px] font-mono">
          {txns.length === 0 && <span className="text-ink-subtle">No transactions.</span>}
          {txns.map((t) => (
            <div key={t.id} className="text-ink-muted flex justify-between">
              <span>{t.type}</span>
              <span className="tnum">${t.amount_usd.toFixed(2)}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
