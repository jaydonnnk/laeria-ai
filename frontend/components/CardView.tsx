"use client";

import { useState } from "react";
import { api, Card, CardDetails, CardTransaction } from "../lib/api";

// Card-shaped visual for an issued disposable card. Credentials are never in
// the row data — "Reveal" live-fetches PAN/CVC from the issuer and holds them
// in component state only (gone on unmount/refresh).

const STATUS_COLOR: Record<Card["status"], string> = {
  issued: "#9a6700",
  active: "#1a7f37",
  canceled: "#888",
};

export default function CardView({
  card,
  onChanged,
  onError,
}: {
  card: Card;
  onChanged: () => Promise<void>;
  onError: (e: string) => void;
}) {
  const [details, setDetails] = useState<CardDetails | null>(null);
  const [txns, setTxns] = useState<CardTransaction[] | null>(null);
  const [busy, setBusy] = useState(false);

  async function reveal() {
    setBusy(true);
    try {
      setDetails(await api.cardDetails(card.id));
    } catch (err) {
      onError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  async function cancel() {
    setBusy(true);
    try {
      await api.cancelCard(card.id);
      setDetails(null);
      await onChanged();
    } catch (err) {
      onError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  async function loadTxns() {
    setBusy(true);
    try {
      setTxns(await api.cardTransactions(card.id));
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
      <div
        style={{
          width: 320,
          borderRadius: 14,
          padding: "18px 20px",
          background: card.status === "canceled" ? "#6e7781" : "#1f2328",
          color: "#fff",
          fontFamily: "ui-monospace, monospace",
          opacity: card.status === "canceled" ? 0.75 : 1,
        }}
      >
        <div style={{ display: "flex", justifyContent: "space-between", fontSize: 11 }}>
          <span style={{ textTransform: "uppercase", letterSpacing: 1 }}>
            {card.issuer} · disposable
          </span>
          <span style={{ color: "#7ee787" }}>
            limit ${Number(card.spend_limit_usd).toFixed(2)}
          </span>
        </div>
        <div style={{ fontSize: 19, letterSpacing: 1, margin: "18px 0 10px" }}>
          {number}
        </div>
        <div style={{ display: "flex", justifyContent: "space-between", fontSize: 12 }}>
          <span>{details ? details.name : "BARYON AGENT"}</span>
          <span>
            {String(card.exp_month).padStart(2, "0")}/{card.exp_year % 100}
            {details && <span> · CVC {details.cvc}</span>}
          </span>
        </div>
      </div>

      <div style={{ display: "flex", gap: 8, marginTop: 8, alignItems: "center" }}>
        <strong style={{ color: STATUS_COLOR[card.status], fontSize: 13 }}>
          {card.status}
        </strong>
        {card.status !== "canceled" && (
          <>
            <button onClick={reveal} disabled={busy || !!details} style={smallButtonStyle}>
              {details ? "Revealed" : "Reveal"}
            </button>
            <button onClick={cancel} disabled={busy} style={smallButtonStyle}>
              Cancel card
            </button>
          </>
        )}
        <button onClick={loadTxns} disabled={busy} style={smallButtonStyle}>
          Transactions
        </button>
        <span style={{ color: "#888", fontSize: 12 }}>
          {new Date(card.created_at).toLocaleString()}
        </span>
      </div>

      {txns && (
        <div style={{ marginTop: 6, fontSize: 13 }}>
          {txns.length === 0 && <span style={{ color: "#888" }}>No transactions.</span>}
          {txns.map((t) => (
            <div key={t.id} style={{ color: "#555" }}>
              {t.type} · ${t.amount_usd.toFixed(2)} ·{" "}
              {new Date(t.created).toLocaleString()}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

const smallButtonStyle: React.CSSProperties = {
  padding: "5px 10px",
  fontSize: 12,
  borderRadius: 7,
  border: "1px solid #ccc",
  background: "#fff",
  cursor: "pointer",
};
