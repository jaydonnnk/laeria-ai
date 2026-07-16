"use client";

import { useCallback, useEffect, useState } from "react";
import { api, BazaarService, Mandate, PayAction, UsageStats } from "../../lib/api";

const STATUS_COLOR: Record<string, string> = {
  executed: "#1a7f37",
  approved: "#0969da",
  pending_approval: "#9a6700",
  cancelled: "#888",
  failed: "#cf222e",
};

const DEFAULT_MANDATE: Mandate = {
  max_per_transaction: 0,
  max_per_month: 0,
  require_confirmation_above: 0,
  allowed_categories: [],
  blocked_vendors: [],
  autonomous_actions_enabled: false,
};

export default function Page() {
  const [mandate, setMandate] = useState<Mandate>(DEFAULT_MANDATE);
  const [actions, setActions] = useState<PayAction[]>([]);
  const [usage, setUsage] = useState<UsageStats | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const refresh = useCallback(async () => {
    try {
      const [m, a, u] = await Promise.all([
        api.getMandate(),
        api.listPayActions(),
        api.usage().catch(() => null),
      ]);
      setMandate({ ...DEFAULT_MANDATE, ...m });
      setActions(a);
      setUsage(u);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  async function saveMandate(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    try {
      await api.putMandate(mandate);
      setInfo("Mandate saved.");
      setTimeout(() => setInfo(null), 3000);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  async function testPurchase() {
    setBusy(true);
    setError(null);
    setInfo(null);
    try {
      const res = await api.proposeAction({
        type: "purchase",
        target_url: "http://localhost:8000/vendor/deep-report",
        category: "research",
        description: "x402 demo: buy the paywalled deep report",
      });
      setInfo(res.outcome);
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      await refresh();
    } finally {
      setBusy(false);
    }
  }

  async function decide(id: string, approve: boolean) {
    setBusy(true);
    setError(null);
    try {
      const res = approve ? await api.approveAction(id) : await api.rejectAction(id);
      setInfo(res.outcome);
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      await refresh();
    } finally {
      setBusy(false);
    }
  }

  const pending = actions.filter((a) => a.status === "pending_approval");

  return (
    <main style={{ maxWidth: 860, margin: "60px auto", padding: "0 24px" }}>
      <a href="/">&larr; back</a>
      <h1>Actions &amp; mandate</h1>
      <p style={{ color: "#555" }}>
        The standing rules your agent must operate within (AP2-style
        co-signed mandate), and every payment action it has taken or wants to
        take. Payments run on real x402 — USDC on Base Sepolia testnet.
      </p>

      {error && <p style={{ color: "#cf222e" }}>{error}</p>}
      {info && <p style={{ color: "#1a7f37" }}>{info}</p>}

      <section style={{ marginBottom: 28 }}>
        <h2>Mandate</h2>
        <form onSubmit={saveMandate} style={{ display: "grid", gap: 10, maxWidth: 480 }}>
          <label style={labelStyle}>
            <input
              type="checkbox"
              checked={mandate.autonomous_actions_enabled}
              onChange={(e) =>
                setMandate({ ...mandate, autonomous_actions_enabled: e.target.checked })
              }
            />{" "}
            Allow autonomous execution (within the limits below)
          </label>
          <label style={labelStyle}>
            Max per transaction ($)
            <input
              type="number" step="0.01" min="0"
              value={mandate.max_per_transaction}
              onChange={(e) =>
                setMandate({ ...mandate, max_per_transaction: Number(e.target.value) })
              }
              style={inputStyle}
            />
          </label>
          <label style={labelStyle}>
            Max per month ($)
            <input
              type="number" step="0.01" min="0"
              value={mandate.max_per_month}
              onChange={(e) => setMandate({ ...mandate, max_per_month: Number(e.target.value) })}
              style={inputStyle}
            />
          </label>
          <label style={labelStyle}>
            Ask me first above ($)
            <input
              type="number" step="0.01" min="0"
              value={mandate.require_confirmation_above}
              onChange={(e) =>
                setMandate({ ...mandate, require_confirmation_above: Number(e.target.value) })
              }
              style={inputStyle}
            />
          </label>
          <button type="submit" disabled={busy} style={buttonStyle}>
            Save mandate
          </button>
        </form>
      </section>

      {pending.length > 0 && (
        <section style={{ marginBottom: 28 }}>
          <h2>Awaiting your approval</h2>
          <div style={{ display: "grid", gap: 12 }}>
            {pending.map((a) => (
              <div key={a.id} style={{ ...cardStyle, borderLeft: "4px solid #9a6700" }}>
                <p style={{ margin: "0 0 6px" }}>
                  <strong>{a.type}</strong> — ${a.amount_usd.toFixed(2)} —{" "}
                  {String(a.metadata?.description ?? a.target)}
                </p>
                <p style={{ margin: "0 0 10px", color: "#888", fontSize: 13 }}>
                  {String(a.metadata?.reason ?? "")} · window expires 10 min after proposal
                </p>
                <span style={{ display: "flex", gap: 8 }}>
                  <button onClick={() => decide(a.id, true)} disabled={busy} style={buttonStyle}>
                    Approve &amp; execute
                  </button>
                  <button onClick={() => decide(a.id, false)} disabled={busy} style={smallButtonStyle}>
                    Reject
                  </button>
                </span>
              </div>
            ))}
          </div>
        </section>
      )}

      <section style={{ marginBottom: 28 }}>
        <h2>Test the rail</h2>
        <p style={{ color: "#555", fontSize: 14 }}>
          Proposes buying the $0.01 paywalled report from the demo vendor. If
          the mandate clears it, the agent pays autonomously with real x402;
          otherwise it lands above for your approval.
        </p>
        <button onClick={testPurchase} disabled={busy} style={buttonStyle}>
          {busy ? "Working…" : "Propose $0.01 test purchase"}
        </button>
      </section>

      <BazaarSection
        onProposed={async (outcome) => {
          setInfo(outcome);
          await refresh();
        }}
        onError={setError}
      />

      {usage && (
        <section style={{ marginBottom: 28 }}>
          <h2>Agent resource usage</h2>
          <div style={{ display: "flex", gap: 12, flexWrap: "wrap" }}>
            {[
              ["Reddit requests", usage.reddit_requests.toLocaleString()],
              ["LLM calls", usage.llm_calls.toLocaleString()],
              ["LLM tokens", usage.llm_tokens.toLocaleString()],
              ["Embedding batches", usage.embed_calls.toLocaleString()],
              ["Paid via x402", `$${usage.paid_usd.toFixed(3)}`],
            ].map(([label, value]) => (
              <div key={label} style={{ ...cardStyle, padding: "10px 16px", minWidth: 140 }}>
                <div style={{ fontSize: 22, fontWeight: 700 }}>{value}</div>
                <div style={{ color: "#888", fontSize: 12 }}>{label}</div>
              </div>
            ))}
          </div>
        </section>
      )}

      <section>
        <h2>History</h2>
        <div style={{ display: "grid", gap: 8 }}>
          {actions.length === 0 && <p style={{ color: "#888" }}>No actions yet.</p>}
          {actions.map((a) => (
            <div key={a.id} style={{ ...cardStyle, padding: "10px 14px" }}>
              <div style={{ display: "flex", justifyContent: "space-between", gap: 12, fontSize: 14 }}>
                <span>
                  <strong style={{ color: STATUS_COLOR[a.status] }}>{a.status}</strong>
                  {" · "}{a.type} · ${a.amount_usd.toFixed(2)}
                  {" · "}{String(a.metadata?.description ?? "")}
                </span>
                <span style={{ color: "#888", fontSize: 12 }}>
                  {new Date(a.created_at).toLocaleString()}
                </span>
              </div>
              {typeof a.metadata?.error === "string" && (
                <p style={{ margin: "4px 0 0", color: "#cf222e", fontSize: 13 }}>
                  {a.metadata.error}
                </p>
              )}
              {typeof a.metadata?.mandate_violation === "string" && (
                <p style={{ margin: "4px 0 0", color: "#cf222e", fontSize: 13 }}>
                  refused: {a.metadata.mandate_violation}
                </p>
              )}
            </div>
          ))}
        </div>
      </section>
    </main>
  );
}

function BazaarSection({
  onProposed,
  onError,
}: {
  onProposed: (outcome: string) => Promise<void>;
  onError: (e: string) => void;
}) {
  const [q, setQ] = useState("");
  const [results, setResults] = useState<BazaarService[] | null>(null);
  const [searching, setSearching] = useState(false);
  const [paying, setPaying] = useState<string | null>(null);

  async function search(e?: React.FormEvent) {
    e?.preventDefault();
    setSearching(true);
    try {
      setResults(await api.bazaarSearch(q, 20));
    } catch (err) {
      onError(err instanceof Error ? err.message : String(err));
    } finally {
      setSearching(false);
    }
  }

  async function pay(s: BazaarService) {
    setPaying(s.resource);
    try {
      const res = await api.proposeAction({
        type: "purchase",
        target_url: s.resource,
        category: "bazaar",
        description: `[Bazaar] ${s.description || s.resource}`.slice(0, 200),
      });
      await onProposed(res.outcome);
    } catch (err) {
      onError(err instanceof Error ? err.message : String(err));
    } finally {
      setPaying(null);
    }
  }

  return (
    <section style={{ marginBottom: 28 }}>
      <h2>Discover x402 services (Bazaar)</h2>
      <p style={{ color: "#555", fontSize: 14 }}>
        Real third-party services that accept agent payments — from
        Coinbase&apos;s public index. Most run on Base <em>mainnet</em>: paying
        them needs real USDC in the agent wallet (mandate caps apply).
      </p>
      <form onSubmit={search} style={{ display: "flex", gap: 8, marginBottom: 12 }}>
        <input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder='Filter, e.g. "chain", "search", "mail"'
          style={{ ...inputStyle, maxWidth: 320 }}
        />
        <button type="submit" disabled={searching} style={smallButtonStyle}>
          {searching ? "Searching…" : "Browse index"}
        </button>
      </form>
      {results && results.length === 0 && (
        <p style={{ color: "#888" }}>No listings matched.</p>
      )}
      {results && results.length > 0 && (
        <div style={{ display: "grid", gap: 8 }}>
          {results.map((s) => (
            <div key={s.resource} style={{ ...cardStyle, padding: "10px 14px" }}>
              <div style={{ display: "flex", justifyContent: "space-between", gap: 12, alignItems: "baseline" }}>
                <span style={{ fontSize: 14, wordBreak: "break-all" }}>
                  {s.resource}
                  <span style={{ color: "#888", fontSize: 12 }}>
                    {" "}· ${s.amount_usd} · {s.network}
                  </span>
                </span>
                {s.payable_by_agent ? (
                  <button
                    onClick={() => pay(s)}
                    disabled={paying !== null}
                    style={smallButtonStyle}
                  >
                    {paying === s.resource ? "Paying…" : "Pay via agent"}
                  </button>
                ) : (
                  <span style={{ color: "#aaa", fontSize: 12, whiteSpace: "nowrap" }}>
                    not agent-payable
                  </span>
                )}
              </div>
              {s.description && (
                <p style={{ margin: "4px 0 0", color: "#555", fontSize: 13 }}>{s.description}</p>
              )}
            </div>
          ))}
        </div>
      )}
    </section>
  );
}

const cardStyle: React.CSSProperties = {
  border: "1px solid #e1e4e8",
  borderRadius: 10,
  padding: "14px 18px",
};

const labelStyle: React.CSSProperties = {
  fontSize: 14,
  color: "#333",
  display: "grid",
  gap: 4,
};

const inputStyle: React.CSSProperties = {
  padding: "8px 10px",
  fontSize: 14,
  border: "1px solid #ccc",
  borderRadius: 8,
  fontFamily: "inherit",
  maxWidth: 200,
};

const buttonStyle: React.CSSProperties = {
  padding: "9px 14px",
  fontSize: 14,
  fontWeight: 600,
  borderRadius: 8,
  border: "none",
  background: "#1f2328",
  color: "#fff",
  cursor: "pointer",
  width: "fit-content",
};

const smallButtonStyle: React.CSSProperties = {
  padding: "9px 14px",
  fontSize: 14,
  borderRadius: 8,
  border: "1px solid #ccc",
  background: "#fff",
  cursor: "pointer",
};
