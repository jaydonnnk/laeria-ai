"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { api, ResearchBrief } from "../../lib/api";
import { Sources } from "../../components/Sources";
import { Button } from "../../components/ui/Button";
import { Card } from "../../components/ui/Card";
import { Badge } from "../../components/ui/Badge";
import { Banner } from "../../components/ui/Banner";
import { Input, Textarea, Field } from "../../components/ui/Input";

/** Turn a consensus sentence into a short, searchable store query. */
function searchSeed(pick: string): string {
  return pick
    .replace(/[^\w\s-]/g, " ")
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 5)
    .join(" ");
}

export default function DecisionPage() {
  const router = useRouter();
  const [query, setQuery] = useState("");
  const [context, setContext] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [brief, setBrief] = useState<ResearchBrief | null>(null);
  const [actOutcome, setActOutcome] = useState<string | null>(null);
  const [acting, setActing] = useState(false);

  async function executePurchase() {
    if (!brief || acting) return;
    setActing(true);
    setActOutcome(null);
    try {
      const res = await api.actOnBrief({
        query,
        consensus_pick: brief.consensus_pick,
        confidence: brief.confidence,
      });
      setActOutcome(res.outcome);
    } catch (err) {
      setActOutcome(err instanceof Error ? err.message : String(err));
    } finally {
      setActing(false);
    }
  }

  async function run(e: React.FormEvent) {
    e.preventDefault();
    if (!query.trim() || loading) return;
    setLoading(true);
    setError(null);
    setBrief(null);
    try {
      setBrief(await api.startDecision(query.trim(), context.trim()));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }

  return (
    <main className="max-w-[760px] mx-auto px-6 py-10 md:py-14">
      <div className="mb-8">
        <div className="eyebrow mb-3">Reddit consensus</div>
        <h1 className="text-2xl md:text-[2rem] font-semibold tracking-[-0.02em]">
          What to buy
        </h1>
        <p className="mt-3 text-ink-muted">
          Describe a purchase. The agent reads real Reddit threads across
          relevant communities and returns the honest consensus — not marketing,
          not SEO spam.
        </p>
      </div>

      <Card className="p-6 mb-8">
        <form onSubmit={run} className="grid gap-4">
          <Field label="What are you deciding?">
            <Textarea
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder='e.g. "best budget mechanical keyboard for programming under $100"'
              rows={2}
              required
              minLength={3}
            />
          </Field>
          <Field label="Context (optional)">
            <Input
              value={context}
              onChange={(e) => setContext(e.target.value)}
              placeholder="Budget, use case, constraints"
            />
          </Field>
          <Button type="submit" disabled={loading || !query.trim()} className="w-fit">
            {loading ? "Researching…" : "Research"}
          </Button>
        </form>
      </Card>

      {loading && (
        <Banner tone="info" className="mb-6">
          Reading Reddit threads — 30–90 seconds. Identifying communities, pulling
          threads, synthesising.
        </Banner>
      )}
      {error && <Banner tone="error" className="mb-6">Research failed: {error}</Banner>}

      {brief && <BriefCard brief={brief} />}

      {brief && brief.consensus_pick && brief.confidence !== "low" && (
        <Card className="mt-6 p-6 border-accent/30 bg-accent-soft">
          <h3 className="font-semibold text-ink mb-1">Consensus is strong — let the agent buy it</h3>
          <p className="text-sm text-ink-muted mb-4">
            The agent finds the pick on the store, selects the best available
            match, mints a single-use card capped to the price, and checks out —
            all under your mandate. Anything above your confirm threshold waits
            for your approval instead of executing.
          </p>
          <div className="flex flex-wrap items-center gap-2">
            <Button
              onClick={() =>
                router.push(
                  `/commerce?q=${encodeURIComponent(searchSeed(brief.consensus_pick))}&auto=1`
                )
              }
            >
              Buy the pick →
            </Button>
            <Button
              variant="secondary"
              onClick={() =>
                router.push(`/commerce?q=${encodeURIComponent(searchSeed(brief.consensus_pick))}`)
              }
            >
              Browse the store first
            </Button>
            <Button variant="secondary" onClick={executePurchase} disabled={acting}>
              {acting ? "Proposing…" : "Pay via x402 (demo vendor)"}
            </Button>
          </div>
          {actOutcome && <p className="mt-3 text-sm text-success">{actOutcome}</p>}
        </Card>
      )}
    </main>
  );
}

function BriefCard({ brief }: { brief: ResearchBrief }) {
  const sq = brief.signal_quality;
  return (
    <div className="grid gap-4">
      <Card className="p-6">
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-lg font-semibold">Verdict</h2>
          <Badge status={brief.confidence}>{brief.confidence} confidence</Badge>
        </div>
        <p className="text-[17px] leading-relaxed text-ink">
          {brief.consensus_pick || "No clear community consensus found."}
        </p>
        <p className="text-[13px] text-ink-subtle mt-2">
          Based on <span className="tnum">{sq.thread_count}</span> threads across{" "}
          {sq.subreddits_checked.map((s) => `r/${s}`).join(", ")}
          {sq.date_range ? ` · ${sq.date_range}` : ""}
        </p>
      </Card>

      <ListSection title="What users praise" items={brief.strengths} tone="success" />
      <ListSection title="Red flags" items={brief.red_flags} tone="danger" />
      <ListSection title="Known failure modes" items={brief.failure_modes} />
      <ListSection title="What review sites miss" items={brief.what_reviewers_miss} />
      <ListSection title="Alternatives the community mentions" items={brief.alternatives} />

      {sq.bias_notes && (
        <Card className="p-5 bg-warning-soft border-warning/20">
          <div className="eyebrow mb-2">Signal quality note</div>
          <p className="text-sm text-ink-muted">{sq.bias_notes}</p>
        </Card>
      )}

      <Sources sources={brief.sources} />
    </div>
  );
}

function ListSection({
  title,
  items,
  tone,
}: {
  title: string;
  items: string[];
  tone?: "success" | "danger";
}) {
  if (!items?.length) return null;
  const dot =
    tone === "success" ? "bg-success" : tone === "danger" ? "bg-danger" : "bg-ink-subtle";
  return (
    <Card className="p-5">
      <h3 className="font-semibold text-ink mb-3">{title}</h3>
      <ul className="grid gap-2">
        {items.map((item, i) => (
          <li key={i} className="flex gap-2.5 text-sm leading-snug text-ink-muted">
            <span className={`mt-1.5 w-1.5 h-1.5 rounded-full shrink-0 ${dot}`} />
            {item}
          </li>
        ))}
      </ul>
    </Card>
  );
}
