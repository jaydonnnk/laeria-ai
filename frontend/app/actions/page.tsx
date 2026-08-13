"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { api, BazaarService, Mandate, PayAction, UsageStats } from "../../lib/api";
import { Button } from "../../components/ui/Button";
import { Card } from "../../components/ui/Card";
import { Badge } from "../../components/ui/Badge";
import { Stat } from "../../components/ui/Stat";
import { Banner, SectionHeader } from "../../components/ui/Banner";
import { Input, Field } from "../../components/ui/Input";
import { shake } from "../../lib/motion";

const STATUS_TONE: Record<string, "success" | "info" | "warning" | "neutral" | "danger"> = {
  executed: "success",
  approved: "info",
  pending_approval: "warning",
  cancelled: "neutral",
  failed: "danger",
};

const DEFAULT_MANDATE: Mandate = {
  max_per_transaction: null,
  max_per_month: null,
  require_confirmation_above: null,
  allowed_categories: [],
  blocked_vendors: [],
  autonomous_actions_enabled: false,
};

export default function ActionsPage() {
  const [mandate, setMandate] = useState<Mandate>(DEFAULT_MANDATE);
  const [actions, setActions] = useState<PayAction[]>([]);
  const [usage, setUsage] = useState<UsageStats | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const historyRef = useRef<HTMLDivElement>(null);

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
      if (res.action.status === "cancelled") {
        shake(historyRef.current);
        setError(String(res.action.metadata?.mandate_violation ?? res.outcome));
      } else {
        setInfo(res.outcome);
      }
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
    <main className="max-w-[1100px] mx-auto px-6 py-10 md:py-14">
      <div className="mb-8">
        <div className="eyebrow mb-3">Mandate &amp; payments</div>
        <h1 className="text-2xl md:text-[2rem] font-semibold tracking-[-0.02em]">
          Actions &amp; mandate
        </h1>
        <p className="mt-3 text-ink-muted max-w-[46rem]">
          The standing rules your agent operates within — a co-signed spending
          mandate — and every payment it has taken or wants to take. Real x402,
          XSGD on Avalanche.
        </p>
      </div>

      {error && <Banner tone="error" className="mb-3">{error}</Banner>}
      {info && <Banner tone="success" className="mb-3">{info}</Banner>}

      <div className="grid lg:grid-cols-[minmax(0,1fr)_320px] gap-10">
        <div>
          {/* Mandate form */}
          <section className="mb-10">
            <SectionHeader title="Mandate" aside="the guardrail" />
            <Card className="p-6">
              <form onSubmit={saveMandate} className="grid gap-5 max-w-[440px]">
                <label className="flex items-start gap-3 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={mandate.autonomous_actions_enabled}
                    onChange={(e) =>
                      setMandate({ ...mandate, autonomous_actions_enabled: e.target.checked })
                    }
                    className="mt-1 accent-[var(--color-accent)] w-4 h-4"
                  />
                  <span className="text-sm text-ink">
                    Allow autonomous execution
                    <span className="block text-ink-subtle text-[13px] mt-0.5">
                      within the caps below · the master switch
                    </span>
                  </span>
                </label>
                <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
                  <CapField
                    label="Per transaction"
                    value={mandate.max_per_transaction}
                    onChange={(v) => setMandate({ ...mandate, max_per_transaction: v })}
                  />
                  <CapField
                    label="Per month"
                    value={mandate.max_per_month}
                    onChange={(v) => setMandate({ ...mandate, max_per_month: v })}
                  />
                  <CapField
                    label="Ask first above"
                    value={mandate.require_confirmation_above}
                    onChange={(v) => setMandate({ ...mandate, require_confirmation_above: v })}
                  />
                </div>
                <p className="text-[13px] text-ink-muted">
                  An empty cap is <b>no allowance</b>, not unlimited — the agent
                  cannot spend against a limit you never set. There is no value
                  that means unlimited.
                </p>
                <Button type="submit" disabled={busy} className="w-fit">
                  Save mandate
                </Button>
              </form>
            </Card>
          </section>

          {/* Pending approvals */}
          {pending.length > 0 && (
            <section className="mb-10">
              <SectionHeader title="Awaiting your approval" aside={`${pending.length} pending`} />
              <div className="grid gap-3">
                {pending.map((a) => (
                  <Card key={a.id} className="p-5 border-l-2 border-l-warning">
                    <div className="flex items-center gap-2 flex-wrap mb-1">
                      <Badge tone="warning">{a.type}</Badge>
                      <span className="tnum text-ink font-medium">${a.amount_usd.toFixed(2)}</span>
                      <span className="text-sm text-ink-muted">
                        {String(a.metadata?.description ?? a.target)}
                      </span>
                    </div>
                    <p className="text-[13px] text-ink-subtle mb-3">
                      {String(a.metadata?.reason ?? "")} · window expires 10 min after proposal
                    </p>
                    <div className="flex gap-2">
                      <Button onClick={() => decide(a.id, true)} disabled={busy}>
                        Approve &amp; execute
                      </Button>
                      <Button variant="secondary" onClick={() => decide(a.id, false)} disabled={busy}>
                        Reject
                      </Button>
                    </div>
                  </Card>
                ))}
              </div>
            </section>
          )}

          {/* Test rail */}
          <section className="mb-10">
            <SectionHeader title="Test the rail" aside="real x402 · $0.01" />
            <Card className="p-5">
              <p className="text-sm text-ink-muted mb-4 max-w-[42rem]">
                Proposes buying the $0.01 paywalled report from the demo vendor.
                If the mandate clears it, the agent pays autonomously; otherwise
                it lands above for your approval.
              </p>
              <Button variant="secondary" onClick={testPurchase} disabled={busy}>
                {busy ? "Working…" : "Propose $0.01 test purchase"}
              </Button>
            </Card>
          </section>

          <BazaarSection
            onProposed={async (outcome) => {
              setInfo(outcome);
              await refresh();
            }}
            onError={setError}
          />

          {/* History */}
          <section ref={historyRef}>
            <SectionHeader title="History" aside="every action, every rail" />
            <div className="grid gap-2">
              {actions.length === 0 && <p className="text-ink-subtle text-sm">No actions yet.</p>}
              {actions.map((a) => (
                <Card key={a.id} className="px-4 py-3">
                  <div className="flex items-start justify-between gap-3">
                    <div className="flex items-center gap-2 flex-wrap">
                      <Badge tone={STATUS_TONE[a.status] ?? "neutral"}>
                        {a.status.replace("_", " ")}
                      </Badge>
                      <span className="text-sm text-ink-muted">{a.type}</span>
                      <span className="tnum text-sm text-ink">${a.amount_usd.toFixed(2)}</span>
                      <span className="text-sm text-ink-subtle">
                        {String(a.metadata?.description ?? "")}
                      </span>
                    </div>
                    <span className="tnum text-xs text-ink-subtle whitespace-nowrap">
                      {new Date(a.created_at).toLocaleString()}
                    </span>
                  </div>
                  {typeof a.metadata?.mandate_violation === "string" && (
                    <p className="mt-1.5 text-[13px] text-danger">
                      refused: {a.metadata.mandate_violation}
                    </p>
                  )}
                  {typeof a.metadata?.error === "string" && (
                    <p className="mt-1.5 text-[13px] text-danger">{a.metadata.error}</p>
                  )}
                </Card>
              ))}
            </div>
          </section>
        </div>

        {/* Usage rail */}
        <aside className="lg:sticky lg:top-20 lg:self-start">
          <SectionHeader title="Agent usage" />
          {usage ? (
            <Card className="p-5 grid gap-5">
              <Stat value={usage.reddit_requests} label="Reddit requests" decimals={0} />
              <Stat value={usage.llm_calls} label="LLM calls" decimals={0} />
              <Stat value={usage.llm_tokens} label="LLM tokens" decimals={0} />
              <Stat value={usage.embed_calls} label="Embedding batches" decimals={0} />
              <Stat value={usage.paid_usd} label="Paid via x402" prefix="$" decimals={3} />
            </Card>
          ) : (
            <Card className="p-5 text-sm text-ink-subtle">Usage unavailable.</Card>
          )}
        </aside>
      </div>
    </main>
  );
}

// An empty field submits null, which the backend reads as ZERO ALLOWANCE.
// There is deliberately no input that means "unlimited".
function CapField({
  label,
  value,
  onChange,
}: {
  label: string;
  value: number | null;
  onChange: (v: number | null) => void;
}) {
  return (
    <Field label={label}>
      <div
        className={`flex items-center bg-surface border rounded-[--radius] overflow-hidden ${
          value === null ? "border-warning/50" : "border-hairline-strong"
        }`}
      >
        <span className="px-2.5 text-ink-subtle text-sm border-r border-hairline">$</span>
        <input
          type="number" step="0.01" min="0"
          value={value ?? ""}
          placeholder="not set"
          onChange={(e) =>
            onChange(e.target.value === "" ? null : Number(e.target.value))
          }
          className="tnum w-full px-2.5 py-2 text-sm bg-transparent outline-none"
        />
      </div>
      {value === null && (
        <span className="text-[12px] text-warning mt-1 block">
          not set → nothing may be spent
        </span>
      )}
    </Field>
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
    <section className="mb-10">
      <SectionHeader title="Discover x402 services" aside="Coinbase Bazaar index" />
      <p className="text-sm text-ink-muted mb-4 max-w-[46rem]">
        Real third-party services that accept agent payments. These are other
        people&apos;s listings on other people&apos;s chains — most price in
        USDC on Base <em>mainnet</em>, so paying one needs real funds on that
        network, not the XSGD this agent runs on. Mandate caps still apply.
      </p>
      <form onSubmit={search} className="flex gap-2 mb-4 max-w-[440px]">
        <Input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder='Filter, e.g. "chain", "search", "mail"'
        />
        <Button type="submit" variant="secondary" disabled={searching} className="shrink-0">
          {searching ? "Searching…" : "Browse"}
        </Button>
      </form>
      {results && results.length === 0 && (
        <p className="text-ink-subtle text-sm">No listings matched.</p>
      )}
      {results && results.length > 0 && (
        <div className="grid gap-2">
          {results.map((s) => (
            <Card key={s.resource} className="px-4 py-3">
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <div className="font-mono text-[13px] text-ink break-all">{s.resource}</div>
                  <div className="tnum text-xs text-ink-subtle mt-0.5">
                    ${s.amount_usd} · {s.network}
                  </div>
                  {s.description && (
                    <p className="text-[13px] text-ink-muted mt-1">{s.description}</p>
                  )}
                </div>
                {s.payable_by_agent ? (
                  <Button
                    variant="secondary"
                    size="sm"
                    onClick={() => pay(s)}
                    disabled={paying !== null}
                    className="shrink-0"
                  >
                    {paying === s.resource ? "Paying…" : "Pay via agent"}
                  </Button>
                ) : (
                  <span className="text-xs text-ink-subtle whitespace-nowrap shrink-0">
                    not agent-payable
                  </span>
                )}
              </div>
            </Card>
          ))}
        </div>
      )}
    </section>
  );
}
