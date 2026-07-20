"use client";

import { useCallback, useEffect, useState } from "react";
import CardView from "../../components/CardView";
import {
  api,
  Card,
  Mandate,
  PayAction,
  StoreProduct,
  StoreVerification,
  WalletBalances,
} from "../../lib/api";

// Hackathon Phase 1 — Discovery: search the demo storefront, then have the
// agent open the live product page in a real browser to verify price and
// availability (screenshot = audit-trail proof). Later phases grow this page
// into the full fund → discover → issue card → checkout lifecycle.

export default function Page() {
  const [q, setQ] = useState("");
  const [products, setProducts] = useState<StoreProduct[] | null>(null);
  const [searching, setSearching] = useState(false);
  const [verifying, setVerifying] = useState<string | null>(null);
  const [verifications, setVerifications] = useState<Record<string, StoreVerification>>({});
  const [buying, setBuying] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);

  async function search(e?: React.FormEvent) {
    e?.preventDefault();
    setSearching(true);
    setError(null);
    try {
      setProducts(await api.storeSearch(q, 12));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSearching(false);
    }
  }

  async function buy(p: StoreProduct) {
    setBuying(p.handle);
    setError(null);
    setInfo(null);
    try {
      const res = await api.proposeAction({
        type: "purchase",
        target_url: p.url,
        category: "commerce",
        description: `[Store] ${p.title}`.slice(0, 200),
        rail: "card",
        product_handle: p.handle,
        variant_id: p.variant_id,
      });
      const status = res.action.status;
      if (status === "pending_approval") {
        setInfo(
          `${res.outcome} — approve it on the Actions page within 10 minutes.`
        );
      } else {
        setInfo(res.outcome);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBuying(null);
    }
  }

  async function verify(p: StoreProduct) {
    setVerifying(p.handle);
    setError(null);
    try {
      const v = await api.storeVerify(p.handle);
      setVerifications((prev) => ({ ...prev, [p.handle]: v }));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setVerifying(null);
    }
  }

  return (
    <main style={{ maxWidth: 860, margin: "60px auto", padding: "0 24px" }}>
      <a href="/">&larr; back</a>
      <h1>Commerce</h1>
      <p style={{ color: "#555" }}>
        The agent&apos;s shopping surface: search the demo storefront, then let
        the agent scan the live product page in a real browser to verify what a
        human buyer would actually be charged. Checkout with a disposable
        virtual card lands here in a later phase.
      </p>

      {error && <p style={{ color: "#cf222e" }}>{error}</p>}
      {info && <p style={{ color: "#1a7f37" }}>{info}</p>}

      <FundingSection onError={setError} />
      <MandateSummary onError={setError} />

      <h2>2 · Discover</h2>
      <form onSubmit={search} style={{ display: "flex", gap: 8, marginBottom: 16 }}>
        <input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder='Search the store, e.g. "snowboard"'
          style={{ ...inputStyle, maxWidth: 320 }}
        />
        <button type="submit" disabled={searching} style={buttonStyle}>
          {searching ? "Searching…" : "Search store"}
        </button>
      </form>

      {products && products.length === 0 && (
        <p style={{ color: "#888" }}>No products matched.</p>
      )}

      {products && products.length > 0 && (
        <div style={{ display: "grid", gap: 12 }}>
          {products.map((p) => {
            const v = verifications[p.handle];
            return (
              <div key={p.id} style={cardStyle}>
                <div style={{ display: "flex", gap: 14, alignItems: "flex-start" }}>
                  {p.image && (
                    // eslint-disable-next-line @next/next/no-img-element
                    <img
                      src={p.image}
                      alt={p.title}
                      style={{ width: 72, height: 72, objectFit: "cover", borderRadius: 8 }}
                    />
                  )}
                  <div style={{ flex: 1 }}>
                    <div style={{ display: "flex", justifyContent: "space-between", gap: 12 }}>
                      <strong>{p.title}</strong>
                      <span style={{ whiteSpace: "nowrap" }}>
                        ${p.price_usd.toFixed(2)}{" "}
                        {!p.available && (
                          <span style={{ color: "#cf222e", fontSize: 12 }}>sold out</span>
                        )}
                      </span>
                    </div>
                    <p style={{ margin: "2px 0 8px", color: "#888", fontSize: 13 }}>
                      {p.vendor}
                      {p.product_type ? ` · ${p.product_type}` : ""}
                    </p>
                    <span style={{ display: "flex", gap: 8, alignItems: "center" }}>
                      <button
                        onClick={() => verify(p)}
                        disabled={verifying !== null}
                        style={smallButtonStyle}
                      >
                        {verifying === p.handle
                          ? "Agent scanning page…"
                          : "Verify with agent"}
                      </button>
                      <button
                        onClick={() => buy(p)}
                        disabled={buying !== null || !p.available}
                        style={{ ...smallButtonStyle, background: "#1f2328", color: "#fff", border: "none" }}
                      >
                        {buying === p.handle ? "Agent buying…" : "Buy with agent"}
                      </button>
                      <a href={p.url} target="_blank" rel="noreferrer" style={{ fontSize: 13 }}>
                        open page
                      </a>
                    </span>
                  </div>
                </div>

                {v && (
                  <div
                    style={{
                      marginTop: 12,
                      paddingTop: 12,
                      borderTop: "1px solid #e1e4e8",
                    }}
                  >
                    <p style={{ margin: "0 0 8px", fontSize: 14 }}>
                      <strong style={{ color: v.price_usd === p.price_usd ? "#1a7f37" : "#cf222e" }}>
                        Verified on live page:
                      </strong>{" "}
                      ${v.price_usd.toFixed(2)} · {v.available ? "in stock" : "unavailable"}
                      {v.price_usd !== p.price_usd && (
                        <span style={{ color: "#cf222e" }}>
                          {" "}
                          — differs from listing (${p.price_usd.toFixed(2)})!
                        </span>
                      )}
                    </p>
                    {/* eslint-disable-next-line @next/next/no-img-element */}
                    <img
                      src={v.screenshot_data_url}
                      alt={`Agent screenshot of ${p.title}`}
                      style={{
                        maxWidth: "100%",
                        border: "1px solid #e1e4e8",
                        borderRadius: 8,
                      }}
                    />
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}

      <CardsSection onError={setError} />

      <ExecutionLog onError={setError} />
    </main>
  );
}

function FundingSection({ onError }: { onError: (e: string) => void }) {
  const [balances, setBalances] = useState<WalletBalances | null>(null);
  const [amount, setAmount] = useState("1");
  const [funding, setFunding] = useState(false);
  const [fundMsg, setFundMsg] = useState<React.ReactNode>(null);

  const refresh = useCallback(async () => {
    try {
      setBalances(await api.walletBalances());
    } catch (err) {
      onError(err instanceof Error ? err.message : String(err));
    }
  }, [onError]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  async function fund(e: React.FormEvent) {
    e.preventDefault();
    setFunding(true);
    setFundMsg(null);
    try {
      const res = await api.walletFund(Number(amount));
      setFundMsg(
        <>
          Funded ${res.amount_usd.toFixed(2)} USDC —{" "}
          <a href={res.explorer_url} target="_blank" rel="noreferrer">
            view transaction
          </a>{" "}
          (balance updates once the block confirms)
        </>
      );
      setTimeout(refresh, 6000);
    } catch (err) {
      onError(err instanceof Error ? err.message : String(err));
    } finally {
      setFunding(false);
    }
  }

  return (
    <section style={{ marginBottom: 28 }}>
      <h2>1 · Fund</h2>
      <p style={{ color: "#555", fontSize: 14 }}>
        Stablecoin funding on {balances?.network ?? "…"} — treasury tops up the
        agent&apos;s USDC. (At the event this leg rides StraitsX: fiat → XUSD →
        agent.)
      </p>
      {balances && (
        <div style={{ display: "flex", gap: 12, flexWrap: "wrap", marginBottom: 12 }}>
          {(["agent", "treasury"] as const).map((role) => {
            const p = balances[role];
            return (
              <div key={role} style={{ ...cardStyle, padding: "10px 16px", minWidth: 210 }}>
                <div style={{ color: "#888", fontSize: 12, textTransform: "uppercase" }}>
                  {role}
                </div>
                {p.error ? (
                  <div style={{ color: "#cf222e", fontSize: 13 }}>{p.error}</div>
                ) : (
                  <>
                    <div style={{ fontSize: 20, fontWeight: 700 }}>
                      {p.usdc?.toFixed(2)} USDC
                    </div>
                    <div style={{ color: "#888", fontSize: 12 }}>
                      {p.native?.toFixed(6)} {balances.native_symbol} ·{" "}
                      {p.address.slice(0, 8)}…
                    </div>
                  </>
                )}
              </div>
            );
          })}
        </div>
      )}
      <form onSubmit={fund} style={{ display: "flex", gap: 8, alignItems: "center" }}>
        <label style={{ fontSize: 13, color: "#555" }}>
          Amount ($){" "}
          <input
            type="number" step="0.01" min="0.01"
            value={amount}
            onChange={(e) => setAmount(e.target.value)}
            style={{ ...inputStyle, maxWidth: 100 }}
          />
        </label>
        <button type="submit" disabled={funding} style={smallButtonStyle}>
          {funding ? "Funding…" : "Fund agent (testnet)"}
        </button>
      </form>
      {fundMsg && <p style={{ color: "#1a7f37", fontSize: 14 }}>{fundMsg}</p>}
    </section>
  );
}

function MandateSummary({ onError }: { onError: (e: string) => void }) {
  const [mandate, setMandate] = useState<Mandate | null>(null);

  useEffect(() => {
    api.getMandate().then(
      (m) => setMandate(m as Mandate),
      (err) => onError(err instanceof Error ? err.message : String(err))
    );
  }, [onError]);

  if (!mandate) return null;
  const cap = (v: number) => (v > 0 ? `$${v.toFixed(2)}` : "no cap");
  return (
    <section style={{ marginBottom: 28 }}>
      <h2>Mandate</h2>
      <div style={{ ...cardStyle, padding: "10px 16px", fontSize: 14 }}>
        autonomous:{" "}
        <strong style={{ color: mandate.autonomous_actions_enabled ? "#1a7f37" : "#cf222e" }}>
          {mandate.autonomous_actions_enabled ? "on" : "off"}
        </strong>
        {" · "}per txn: <strong>{cap(mandate.max_per_transaction)}</strong>
        {" · "}per month: <strong>{cap(mandate.max_per_month)}</strong>
        {" · "}ask first above: <strong>{cap(mandate.require_confirmation_above)}</strong>
        {"  "}
        <a href="/actions" style={{ fontSize: 13 }}>edit</a>
      </div>
    </section>
  );
}

function ExecutionLog({ onError }: { onError: (e: string) => void }) {
  const [actions, setActions] = useState<PayAction[] | null>(null);
  const [shots, setShots] = useState<Record<string, string[]>>({});

  useEffect(() => {
    api.listPayActions().then(
      (a) => setActions(a.filter((x) => x.metadata?.rail === "card")),
      (err) => onError(err instanceof Error ? err.message : String(err))
    );
  }, [onError]);

  async function loadShots(a: PayAction) {
    const paths = (a.metadata?.checkout_screenshots as string[]) ?? [];
    try {
      const urls = await Promise.all(
        paths.map((p) => api.screenshotUrl("checkout", p))
      );
      setShots((prev) => ({ ...prev, [a.id]: urls }));
    } catch (err) {
      onError(err instanceof Error ? err.message : String(err));
    }
  }

  if (!actions) return null;
  return (
    <section style={{ marginTop: 32 }}>
      <h2>4 · Execution log</h2>
      {actions.length === 0 && (
        <p style={{ color: "#888" }}>No card purchases yet.</p>
      )}
      <div style={{ display: "grid", gap: 10 }}>
        {actions.map((a) => (
          <div key={a.id} style={{ ...cardStyle, padding: "10px 14px" }}>
            <div style={{ display: "flex", justifyContent: "space-between", gap: 12, fontSize: 14 }}>
              <span>
                <strong style={{ color: EXEC_STATUS_COLOR[a.status] ?? "#555" }}>
                  {a.status}
                </strong>
                {" · "}${a.amount_usd.toFixed(2)}
                {" · "}{String(a.metadata?.description ?? "")}
              </span>
              <span style={{ color: "#888", fontSize: 12 }}>
                {new Date(a.created_at).toLocaleString()}
              </span>
            </div>
            {a.status === "executed" && (
              <p style={{ margin: "4px 0 0", fontSize: 13, color: "#555" }}>
                order <strong>{String(a.metadata?.order_reference ?? "?")}</strong>
                {" · "}card ••••{String(a.metadata?.card_last4 ?? "")}
                {a.metadata?.pan_shim === true && " · bogus-gateway PAN shim (declared)"}
              </p>
            )}
            {typeof a.metadata?.mandate_violation === "string" && (
              <p style={{ margin: "4px 0 0", color: "#cf222e", fontSize: 13 }}>
                refused: {a.metadata.mandate_violation}
              </p>
            )}
            {typeof a.metadata?.error === "string" && (
              <p style={{ margin: "4px 0 0", color: "#cf222e", fontSize: 13 }}>
                {a.metadata.error}
              </p>
            )}
            {Array.isArray(a.metadata?.checkout_screenshots) &&
              (a.metadata.checkout_screenshots as string[]).length > 0 &&
              !shots[a.id] && (
                <button onClick={() => loadShots(a)} style={{ ...smallButtonStyle, marginTop: 6 }}>
                  Show audit screenshots
                </button>
              )}
            {shots[a.id] && (
              <div style={{ display: "flex", gap: 8, marginTop: 8, flexWrap: "wrap" }}>
                {shots[a.id].map((u) => (
                  // eslint-disable-next-line @next/next/no-img-element
                  <img key={u} src={u} alt="checkout stage" style={{ width: 220, border: "1px solid #e1e4e8", borderRadius: 6 }} />
                ))}
              </div>
            )}
          </div>
        ))}
      </div>
    </section>
  );
}

const EXEC_STATUS_COLOR: Record<string, string> = {
  executed: "#1a7f37",
  pending_approval: "#9a6700",
  cancelled: "#888",
  failed: "#cf222e",
};

function CardsSection({ onError }: { onError: (e: string) => void }) {
  const [cards, setCards] = useState<Card[] | null>(null);
  const [limit, setLimit] = useState("25");
  const [issuing, setIssuing] = useState(false);

  const refresh = useCallback(async () => {
    try {
      setCards(await api.listCards());
    } catch (err) {
      onError(err instanceof Error ? err.message : String(err));
    }
  }, [onError]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  async function testIssue(e: React.FormEvent) {
    e.preventDefault();
    setIssuing(true);
    try {
      await api.testIssueCard(Number(limit) || 1, "dev test issue");
      await refresh();
    } catch (err) {
      onError(err instanceof Error ? err.message : String(err));
    } finally {
      setIssuing(false);
    }
  }

  return (
    <section style={{ marginTop: 32 }}>
      <h2>3 · Disposable cards</h2>
      <p style={{ color: "#555", fontSize: 14 }}>
        Virtual cards issued to the agent, one per approved purchase, spending
        limit pinned to the approved amount, canceled after use. Card numbers
        are never stored — &ldquo;Reveal&rdquo; fetches them live from the
        issuer.
      </p>
      <form onSubmit={testIssue} style={{ display: "flex", gap: 8, marginBottom: 16, alignItems: "center" }}>
        <label style={{ fontSize: 13, color: "#555" }}>
          Limit ($){" "}
          <input
            type="number"
            step="0.01"
            min="0.01"
            value={limit}
            onChange={(e) => setLimit(e.target.value)}
            style={{ ...inputStyle, maxWidth: 100 }}
          />
        </label>
        <button type="submit" disabled={issuing} style={smallButtonStyle}>
          {issuing ? "Issuing…" : "Issue test card"}
        </button>
      </form>
      {cards && cards.length === 0 && (
        <p style={{ color: "#888" }}>No cards issued yet.</p>
      )}
      {cards && cards.length > 0 && (
        <div style={{ display: "grid", gap: 20 }}>
          {cards.map((c) => (
            <CardView key={c.id} card={c} onChanged={refresh} onError={onError} />
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

const inputStyle: React.CSSProperties = {
  padding: "8px 10px",
  fontSize: 14,
  border: "1px solid #ccc",
  borderRadius: 8,
  fontFamily: "inherit",
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
};

const smallButtonStyle: React.CSSProperties = {
  padding: "7px 12px",
  fontSize: 13,
  borderRadius: 8,
  border: "1px solid #ccc",
  background: "#fff",
  cursor: "pointer",
};
