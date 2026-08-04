"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import CardView from "../../components/CardView";
import { Button } from "../../components/ui/Button";
import { Card } from "../../components/ui/Card";
import { Badge } from "../../components/ui/Badge";
import { Stat } from "../../components/ui/Stat";
import { Banner, SectionHeader } from "../../components/ui/Banner";
import { Input, Field } from "../../components/ui/Input";
import { Stepper, Step, StepState } from "../../components/commerce/Stepper";
import { dealIn, shake } from "../../lib/motion";
import {
  api,
  Card as CardT,
  Mandate,
  PayAction,
  StoreProduct,
  StoreVerification,
  WalletBalances,
} from "../../lib/api";

// The hackathon showpiece: fund → discover → issue → execute, mandate-enforced.
// Shared design system + a live lifecycle stepper reflecting real state.

export default function CommercePage() {
  const [q, setQ] = useState("");
  const [products, setProducts] = useState<StoreProduct[] | null>(null);
  const [searching, setSearching] = useState(false);
  const [verifying, setVerifying] = useState<string | null>(null);
  const [verifications, setVerifications] = useState<Record<string, StoreVerification>>({});
  const [buying, setBuying] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);
  const [handedPick, setHandedPick] = useState<string | null>(null);
  const [autoNote, setAutoNote] = useState<string | null>(null);

  // lifecycle state — lifted so the stepper reflects the whole flow
  const [agentFunded, setAgentFunded] = useState(false);
  const [cardCount, setCardCount] = useState(0);
  const [executedCount, setExecutedCount] = useState(0);

  const steps: Step[] = useMemo(() => {
    const done = {
      fund: agentFunded,
      discover: !!products?.length || Object.keys(verifications).length > 0,
      issue: cardCount > 0,
      execute: executedCount > 0,
    };
    const order: (keyof typeof done)[] = ["fund", "discover", "issue", "execute"];
    const firstTodo = order.find((k) => !done[k]);
    const state = (k: keyof typeof done): StepState =>
      done[k] ? "done" : k === firstTodo ? "active" : "todo";
    return [
      { key: "fund", label: "Fund", caption: "Stablecoin → agent", state: state("fund") },
      { key: "discover", label: "Discover", caption: "Scan the store", state: state("discover") },
      { key: "issue", label: "Issue", caption: "Disposable card", state: state("issue") },
      { key: "execute", label: "Execute", caption: "Checkout + settle", state: state("execute") },
    ];
  }, [agentFunded, products, verifications, cardCount, executedCount]);

  const runSearch = useCallback(async (term: string): Promise<StoreProduct[]> => {
    setSearching(true);
    setError(null);
    try {
      const res = await api.storeSearch(term, 12);
      setProducts(res);
      return res;
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      return [];
    } finally {
      setSearching(false);
    }
  }, []);

  async function search(e?: React.FormEvent) {
    e?.preventDefault();
    await runSearch(q);
  }

  // Handed a pick from "Worth it?" (/commerce?q=…&auto=1): prefill, search,
  // and — when auto — let the agent choose the best available match and
  // propose the purchase itself. The mandate still gates it: over-threshold
  // buys land in pending approval rather than executing.
  // Read from location rather than useSearchParams to avoid needing a Suspense
  // boundary on this statically-prerendered client page.
  const autoRan = useRef(false);
  useEffect(() => {
    if (autoRan.current) return;
    const params = new URLSearchParams(window.location.search);
    const handed = params.get("q");
    if (!handed) return;
    autoRan.current = true;
    const auto = params.get("auto") === "1";
    setQ(handed);
    setHandedPick(handed);
    (async () => {
      const res = await runSearch(handed);
      if (!auto) return;
      const best = res.find((p) => p.available);
      if (!best) {
        setAutoNote("No available product matched the pick — search manually below.");
        return;
      }
      setAutoNote(
        `Agent selected “${best.title}” ($${best.price_usd.toFixed(2)}) and proposed the purchase.`
      );
      await buy(best, null);
    })();
    // buy/runSearch are stable enough for this one-shot handoff
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runSearch]);

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

  async function buy(p: StoreProduct, btn: HTMLElement | null) {
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
      const a = res.action;
      if (a.status === "cancelled" || a.status === "failed") {
        shake(btn);
        setError(
          String(a.metadata?.mandate_violation ?? a.metadata?.error ?? res.outcome)
        );
      } else if (a.status === "pending_approval") {
        setInfo(`${res.outcome} — approve it on the Actions page within 10 minutes.`);
      } else {
        setInfo(res.outcome);
      }
    } catch (err) {
      shake(btn);
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBuying(null);
    }
  }

  return (
    <main className="max-w-[1100px] mx-auto px-6 py-10 md:py-14">
      <div className="mb-8">
        <div className="eyebrow mb-3">Agentic checkout</div>
        <h1 className="text-2xl md:text-[2rem] font-semibold tracking-[-0.02em]">
          The lifecycle console
        </h1>
        <p className="mt-3 text-ink-muted max-w-[44rem]">
          Fund the agent, let it discover and verify a product, watch it mint a
          single-use card capped to the purchase, and check out — every step
          re-checked against your mandate.
        </p>
      </div>

      {error && <Banner tone="error" className="mb-3">{error}</Banner>}
      {info && <Banner tone="success" className="mb-3">{info}</Banner>}

      <Card className="p-6 mb-10">
        <Stepper steps={steps} />
      </Card>

      <FundingSection onFunded={setAgentFunded} />

      <MandateSummary />

      {/* ---- Discover ---- */}
      <section className="mb-10">
        <SectionHeader n="02" title="Discover" aside="agent scans the live storefront" />
        {handedPick && (
          <Banner tone="info" className="mb-4">
            Carried over from <b>Worth it?</b> — searching the store for{" "}
            <span className="font-mono">{handedPick}</span>.
            {autoNote && <span className="block mt-1">{autoNote}</span>}
          </Banner>
        )}
        <form onSubmit={search} className="flex gap-2 mb-5 max-w-[440px]">
          <Input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder='Search the store, e.g. "snowboard"'
          />
          <Button type="submit" disabled={searching} className="shrink-0">
            {searching ? "Searching…" : "Search"}
          </Button>
        </form>

        {products && products.length === 0 && (
          <p className="text-ink-subtle text-sm">No products matched.</p>
        )}

        {products && products.length > 0 && (
          <div className="grid gap-3">
            {products.map((p) => (
              <ProductRow
                key={p.id}
                product={p}
                verification={verifications[p.handle]}
                verifying={verifying === p.handle}
                buying={buying === p.handle}
                busy={verifying !== null || buying !== null}
                onVerify={() => verify(p)}
                onBuy={(btn) => buy(p, btn)}
              />
            ))}
          </div>
        )}
      </section>

      <CardsSection onCount={setCardCount} />

      <ExecutionLog onExecuted={setExecutedCount} />
    </main>
  );
}

/* ------------------------------------------------------------------ */

function ProductRow({
  product: p,
  verification: v,
  verifying,
  buying,
  busy,
  onVerify,
  onBuy,
}: {
  product: StoreProduct;
  verification?: StoreVerification;
  verifying: boolean;
  buying: boolean;
  busy: boolean;
  onVerify: () => void;
  onBuy: (btn: HTMLElement | null) => void;
}) {
  const buyRef = useRef<HTMLButtonElement>(null);
  const match = v && v.price_usd === p.price_usd;
  return (
    <Card interactive className="p-5">
      <div className="flex gap-4">
        {p.image ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img
            src={p.image}
            alt={p.title}
            className="w-20 h-20 rounded-[--radius] object-cover border border-hairline shrink-0"
          />
        ) : (
          <div className="w-20 h-20 rounded-[--radius] bg-surface-2 border border-hairline shrink-0" />
        )}
        <div className="flex-1 min-w-0">
          <div className="flex items-start justify-between gap-4">
            <div>
              <div className="font-semibold text-ink">{p.title}</div>
              <div className="text-sm text-ink-subtle mt-0.5">
                {p.vendor}
                {p.product_type ? ` · ${p.product_type}` : ""}
              </div>
            </div>
            <div className="tnum text-lg text-ink whitespace-nowrap">
              ${p.price_usd.toFixed(2)}
              {!p.available && <span className="ml-2 text-xs text-danger">sold out</span>}
            </div>
          </div>
          <div className="flex items-center gap-2 mt-4">
            <Button variant="secondary" size="sm" onClick={onVerify} disabled={busy}>
              {verifying ? "Scanning page…" : "Verify with agent"}
            </Button>
            <Button
              ref={buyRef}
              size="sm"
              onClick={() => onBuy(buyRef.current)}
              disabled={busy || !p.available}
            >
              {buying ? "Agent buying…" : "Buy with agent"}
            </Button>
            <a
              href={p.url}
              target="_blank"
              rel="noreferrer"
              className="text-[13px] text-info hover:underline ml-1"
            >
              open page ↗
            </a>
          </div>

          {v && (
            <div className="mt-4 pt-4 border-t border-hairline">
              <div className="flex items-center gap-2 mb-3">
                <Badge tone={match ? "success" : "danger"} dot>
                  verified on live page
                </Badge>
                <span className="tnum text-sm text-ink-muted">
                  ${v.price_usd.toFixed(2)} · {v.available ? "in stock" : "unavailable"}
                </span>
                {!match && (
                  <span className="text-xs text-danger">
                    — differs from listing (${p.price_usd.toFixed(2)})
                  </span>
                )}
              </div>
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img
                src={v.screenshot_data_url}
                alt={`Agent screenshot of ${p.title}`}
                className="max-w-full rounded-[--radius] border border-hairline"
              />
            </div>
          )}
        </div>
      </div>
    </Card>
  );
}

/* ------------------------------------------------------------------ */

/** Inline, section-scoped failure notice.
 *
 *  Every section used to report into one page-level banner, so a single
 *  transient call painted an error across the whole console — including the
 *  parts that had loaded perfectly well. Failures now stay where they
 *  happened and offer a retry. */
function SectionNotice({
  message,
  onRetry,
}: {
  message: string;
  onRetry?: () => void;
}) {
  return (
    <div className="flex items-start gap-3 text-[13px] text-danger bg-danger-soft border border-danger/20 rounded-[--radius] px-3 py-2 mb-3">
      <span className="flex-1 leading-snug">{message}</span>
      {onRetry && (
        <button
          onClick={onRetry}
          className="font-mono text-[11px] uppercase tracking-wide hover:underline shrink-0 mt-px"
        >
          retry
        </button>
      )}
    </div>
  );
}

function FundingSection({ onFunded }: { onFunded: (v: boolean) => void }) {
  const [balances, setBalances] = useState<WalletBalances | null>(null);
  const [amount, setAmount] = useState("1");
  const [funding, setFunding] = useState(false);
  const [fundMsg, setFundMsg] = useState<React.ReactNode>(null);
  const [err, setErr] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const b = await api.walletBalances();
      setBalances(b);
      setErr(null);
      const agentUsdc = "usdc" in b.agent ? b.agent.usdc ?? 0 : 0;
      onFunded(agentUsdc > 0);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  }, [onFunded]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  async function fund(e: React.FormEvent) {
    e.preventDefault();
    setFunding(true);
    setFundMsg(null);
    setErr(null);
    try {
      const res = await api.walletFund(Number(amount));
      setFundMsg(
        <>
          Funded ${res.amount_usd.toFixed(2)} USDC —{" "}
          <a className="text-info hover:underline" href={res.explorer_url} target="_blank" rel="noreferrer">
            view transaction ↗
          </a>{" "}
          (balance updates once the block confirms)
        </>
      );
      setTimeout(refresh, 6000);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setFunding(false);
    }
  }

  return (
    <section className="mb-10">
      <SectionHeader n="01" title="Fund" aside={balances?.network ?? "…"} />
      {err && <SectionNotice message={err} onRetry={refresh} />}
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 mb-4">
        {(["agent", "treasury"] as const).map((role) => {
          const party = balances?.[role];
          return (
            <Card key={role} className="p-5">
              <div className="flex items-center justify-between mb-3">
                <Badge tone={role === "agent" ? "accent" : "neutral"} dot>
                  {role}
                </Badge>
                {party && !party.error && (
                  <span className="tnum text-xs text-ink-subtle">
                    {party.address.slice(0, 8)}…{party.address.slice(-4)}
                  </span>
                )}
              </div>
              {party?.error ? (
                <div className="text-sm text-danger">{party.error}</div>
              ) : party ? (
                <>
                  <Stat value={party.usdc ?? 0} label="USDC balance" prefix="$" />
                  <div className="tnum text-xs text-ink-subtle mt-2">
                    {(party.native ?? 0).toFixed(6)} {balances?.native_symbol} gas
                  </div>
                </>
              ) : (
                <div className="h-14 animate-pulse bg-surface-2 rounded" />
              )}
            </Card>
          );
        })}
      </div>
      <form onSubmit={fund} className="flex items-end gap-3">
        <Field label="Amount (USD)">
          <div className="flex items-center bg-surface border border-hairline-strong rounded-[--radius] overflow-hidden w-[140px]">
            <span className="px-3 text-ink-subtle text-sm border-r border-hairline">$</span>
            <input
              type="number" step="0.01" min="0.01"
              value={amount}
              onChange={(e) => setAmount(e.target.value)}
              className="tnum w-full px-3 py-2 text-sm bg-transparent outline-none"
            />
          </div>
        </Field>
        <Button type="submit" variant="secondary" disabled={funding}>
          {funding ? "Funding…" : "Fund agent (testnet)"}
        </Button>
      </form>
      {fundMsg && <p className="text-sm text-success mt-3">{fundMsg}</p>}
    </section>
  );
}

/* ------------------------------------------------------------------ */

function MandateSummary() {
  const [mandate, setMandate] = useState<Mandate | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const load = useCallback(() => {
    api.getMandate().then(
      (m) => {
        setMandate(m as Mandate);
        setErr(null);
      },
      (e) => setErr(e instanceof Error ? e.message : String(e))
    );
  }, []);
  useEffect(() => {
    load();
  }, [load]);
  if (err) {
    return (
      <section className="mb-10">
        <SectionNotice message={`Mandate unavailable — ${err}`} onRetry={load} />
      </section>
    );
  }
  if (!mandate) return null;
  // An unset cap is zero allowance, so it must never read as permissive.
  const cap = (v: number | null) => (v === null ? "not set" : `$${v.toFixed(2)}`);
  return (
    <section className="mb-10">
      <Card className="px-5 py-3.5 flex flex-wrap items-center gap-x-6 gap-y-2 text-sm">
        <span className="eyebrow">mandate</span>
        <span className="text-ink-muted">
          autonomous{" "}
          <Badge tone={mandate.autonomous_actions_enabled ? "success" : "danger"}>
            {mandate.autonomous_actions_enabled ? "on" : "off"}
          </Badge>
        </span>
        <span className="text-ink-muted">per txn <b className="tnum text-ink">{cap(mandate.max_per_transaction)}</b></span>
        <span className="text-ink-muted">per month <b className="tnum text-ink">{cap(mandate.max_per_month)}</b></span>
        <span className="text-ink-muted">ask first above <b className="tnum text-ink">{cap(mandate.require_confirmation_above)}</b></span>
        <a href="/actions" className="text-info hover:underline ml-auto">edit →</a>
      </Card>
    </section>
  );
}

/* ------------------------------------------------------------------ */

function CardsSection({ onCount }: { onCount: (n: number) => void }) {
  const [cards, setCards] = useState<CardT[] | null>(null);
  const [limit, setLimit] = useState("25");
  const [issuing, setIssuing] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const gridRef = useRef<HTMLDivElement>(null);

  const refresh = useCallback(async () => {
    try {
      const c = await api.listCards();
      setCards(c);
      setErr(null);
      onCount(c.length);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  }, [onCount]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  async function testIssue(e: React.FormEvent) {
    e.preventDefault();
    setIssuing(true);
    setErr(null);
    try {
      await api.testIssueCard(Number(limit) || 1, "dev test issue");
      await refresh();
      // deal-in the newest card
      requestAnimationFrame(() => dealIn(gridRef.current?.firstElementChild as HTMLElement));
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setIssuing(false);
    }
  }

  return (
    <section className="mb-10">
      <SectionHeader n="03" title="Disposable cards" aside="one per purchase · destroyed after use" />
      {err && <SectionNotice message={err} onRetry={refresh} />}
      <p className="text-sm text-ink-muted max-w-[46rem] mb-5">
        Card numbers are never stored — <em>Reveal</em> fetches them live from the
        issuer. Each card&apos;s limit is pinned to its purchase.
      </p>
      <form onSubmit={testIssue} className="flex items-end gap-3 mb-6">
        <Field label="Test limit (USD)">
          <div className="flex items-center bg-surface border border-hairline-strong rounded-[--radius] overflow-hidden w-[140px]">
            <span className="px-3 text-ink-subtle text-sm border-r border-hairline">$</span>
            <input
              type="number" step="0.01" min="0.01"
              value={limit}
              onChange={(e) => setLimit(e.target.value)}
              className="tnum w-full px-3 py-2 text-sm bg-transparent outline-none"
            />
          </div>
        </Field>
        <Button type="submit" variant="secondary" disabled={issuing}>
          {issuing ? "Issuing…" : "Issue test card"}
        </Button>
      </form>
      {cards && cards.length === 0 && (
        <p className="text-ink-subtle text-sm">No cards issued yet.</p>
      )}
      {cards && cards.length > 0 && (
        <div ref={gridRef} className="grid gap-6 sm:grid-cols-2">
          {cards.map((c) => (
            <CardView key={c.id} card={c} onChanged={refresh} onError={setErr} />
          ))}
        </div>
      )}
    </section>
  );
}

/* ------------------------------------------------------------------ */

const EXEC_TONE: Record<string, "success" | "warning" | "neutral" | "danger"> = {
  executed: "success",
  pending_approval: "warning",
  cancelled: "neutral",
  failed: "danger",
};

function ExecutionLog({ onExecuted }: { onExecuted: (n: number) => void }) {
  const [actions, setActions] = useState<PayAction[] | null>(null);
  const [shots, setShots] = useState<Record<string, string[]>>({});
  const [err, setErr] = useState<string | null>(null);

  const load = useCallback(() => {
    api.listPayActions().then(
      (a) => {
        const cardActions = a.filter((x) => x.metadata?.rail === "card");
        setActions(cardActions);
        setErr(null);
        onExecuted(cardActions.filter((x) => x.status === "executed").length);
      },
      (e) => setErr(e instanceof Error ? e.message : String(e))
    );
  }, [onExecuted]);

  useEffect(() => {
    load();
  }, [load]);

  async function loadShots(a: PayAction) {
    const paths = (a.metadata?.checkout_screenshots as string[]) ?? [];
    try {
      const urls = await Promise.all(paths.map((p) => api.screenshotUrl("checkout", p)));
      setShots((prev) => ({ ...prev, [a.id]: urls }));
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  }

  if (err && !actions) {
    return (
      <section>
        <SectionHeader n="04" title="Execution log" aside="card-rail purchases + audit trail" />
        <SectionNotice message={err} onRetry={load} />
      </section>
    );
  }
  if (!actions) return null;
  return (
    <section>
      <SectionHeader n="04" title="Execution log" aside="card-rail purchases + audit trail" />
      {err && <SectionNotice message={err} onRetry={load} />}
      {actions.length === 0 && <p className="text-ink-subtle text-sm">No card purchases yet.</p>}
      <div className="grid gap-3">
        {actions.map((a) => {
          const refused = typeof a.metadata?.mandate_violation === "string";
          return (
            <Card
              key={a.id}
              className={refused ? "p-4 border-danger/30" : "p-4"}
            >
              <div className="flex items-start justify-between gap-3">
                <div className="flex items-center gap-2.5 flex-wrap">
                  <Badge tone={EXEC_TONE[a.status] ?? "neutral"}>{a.status.replace("_", " ")}</Badge>
                  <span className="tnum text-sm text-ink">${a.amount_usd.toFixed(2)}</span>
                  <span className="text-sm text-ink-muted">{String(a.metadata?.description ?? "")}</span>
                </div>
                <span className="tnum text-xs text-ink-subtle whitespace-nowrap">
                  {new Date(a.created_at).toLocaleString()}
                </span>
              </div>
              {a.status === "executed" && (
                <div className="mt-2 text-[13px] text-ink-muted font-mono flex flex-wrap gap-x-4 gap-y-1">
                  <span>order <b className="text-ink">{String(a.metadata?.order_reference ?? "?")}</b></span>
                  <span>card ••••{String(a.metadata?.card_last4 ?? "")}</span>
                  {a.metadata?.pan_shim === true && (
                    <span className="text-ink-subtle">bogus-gateway PAN shim (declared)</span>
                  )}
                </div>
              )}
              {refused && (
                <p className="mt-2 text-[13px] text-danger">
                  refused: {String(a.metadata?.mandate_violation)}
                </p>
              )}
              {typeof a.metadata?.error === "string" && (
                <p className="mt-2 text-[13px] text-danger">{a.metadata.error}</p>
              )}
              {Array.isArray(a.metadata?.checkout_screenshots) &&
                (a.metadata.checkout_screenshots as string[]).length > 0 &&
                !shots[a.id] && (
                  <Button variant="ghost" size="sm" className="mt-2 -ml-2" onClick={() => loadShots(a)}>
                    Show audit screenshots
                  </Button>
                )}
              {shots[a.id] && (
                <div className="flex gap-2 mt-3 flex-wrap">
                  {shots[a.id].map((u) => (
                    // eslint-disable-next-line @next/next/no-img-element
                    <img
                      key={u}
                      src={u}
                      alt="checkout stage"
                      className="w-56 rounded-[--radius] border border-hairline"
                    />
                  ))}
                </div>
              )}
            </Card>
          );
        })}
      </div>
    </section>
  );
}
