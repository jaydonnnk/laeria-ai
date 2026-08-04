"use client";

import { useState } from "react";
import { api, OutcomeSummary } from "../../lib/api";
import { Sources } from "../../components/Sources";
import { Button } from "../../components/ui/Button";
import { Card } from "../../components/ui/Card";
import { Badge } from "../../components/ui/Badge";
import { Banner } from "../../components/ui/Banner";
import { Input, Textarea, Field } from "../../components/ui/Input";

export default function ResearchPage() {
  const [decision, setDecision] = useState("");
  const [context, setContext] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [summary, setSummary] = useState<OutcomeSummary | null>(null);
  const [elapsed, setElapsed] = useState(0);

  async function run(e: React.FormEvent) {
    e.preventDefault();
    if (!decision.trim() || loading) return;
    setLoading(true);
    setError(null);
    setSummary(null);
    setElapsed(0);
    try {
      setSummary(
        await api.startRetrospective(decision.trim(), context.trim(), 8, setElapsed)
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }

  return (
    <main className="max-w-[760px] mx-auto px-6 py-10 md:py-14">
      <div className="mb-8">
        <div className="eyebrow mb-3">Real outcomes</div>
        <h1 className="text-2xl md:text-[2rem] font-semibold tracking-[-0.02em]">
          How it went
        </h1>
        <p className="mt-3 text-ink-muted">
          Describe a decision you&apos;re about to make. The agent hunts update
          posts — real people who made the same call and came back to report.
          Not opinions. Outcomes.
        </p>
      </div>

      <Card className="p-6 mb-8">
        <form onSubmit={run} className="grid gap-4">
          <Field label="The decision">
            <Textarea
              value={decision}
              onChange={(e) => setDecision(e.target.value)}
              placeholder='e.g. "getting LASIK eye surgery" or "dropping out to work full-time"'
              rows={2}
              required
              minLength={3}
            />
          </Field>
          <Field label="Context (optional)">
            <Input
              value={context}
              onChange={(e) => setContext(e.target.value)}
              placeholder="About your situation"
            />
          </Field>
          <Button type="submit" disabled={loading || !decision.trim()} className="w-fit">
            {loading ? "Mining update posts…" : "Find outcomes"}
          </Button>
        </form>
      </Card>

      {loading && (
        <Banner tone="info" className="mb-6">
          Hunting retrospective posts across Reddit — the slowest mode, 2–4
          minutes. Searching outcome phrasings, classifying genuine reports,
          reading the strongest.
          <span className="tnum ml-2 text-ink-subtle">{elapsed.toFixed(0)}s</span>
        </Banner>
      )}
      {error && <Banner tone="error" className="mb-6">Research failed: {error}</Banner>}

      {summary && <OutcomesCard s={summary} />}
    </main>
  );
}

function OutcomesCard({ s }: { s: OutcomeSummary }) {
  const pctFmt = (x: number) => `${Math.round(x * 100)}%`;
  return (
    <div className="grid gap-4">
      {s.thin_coverage && (
        <Banner tone="info">
          <b>Limited data.</b> Fewer than 5 genuine outcome reports were found.
          What follows is the honest best from a thin sample — anecdote, not
          consensus.
        </Banner>
      )}

      <Card className="p-6">
        <div className="flex items-center justify-between gap-4 mb-4">
          <h2 className="text-lg font-semibold">
            <span className="tnum">{s.retrospective_count}</span> outcome report
            {s.retrospective_count === 1 ? "" : "s"} ·{" "}
            <span className="tnum">{s.threads_read}</span> read in full
          </h2>
          <Badge status={s.confidence}>{s.confidence} confidence</Badge>
        </div>

        <div className="flex rounded-[--radius-sm] overflow-hidden h-7 mb-3">
          {s.pct_positive > 0 && (
            <div
              className="tnum flex items-center justify-center text-white text-xs font-semibold bg-success min-w-0"
              style={{ flex: s.pct_positive }}
            >
              {pctFmt(s.pct_positive)}
            </div>
          )}
          {s.pct_mixed > 0 && (
            <div
              className="tnum flex items-center justify-center text-white text-xs font-semibold bg-warning min-w-0"
              style={{ flex: s.pct_mixed }}
            >
              {pctFmt(s.pct_mixed)}
            </div>
          )}
          {s.pct_negative > 0 && (
            <div
              className="tnum flex items-center justify-center text-white text-xs font-semibold bg-danger min-w-0"
              style={{ flex: s.pct_negative }}
            >
              {pctFmt(s.pct_negative)}
            </div>
          )}
        </div>
        <p className="text-[13px] text-ink-subtle">
          <span className="text-success">■</span> glad they did it&ensp;
          <span className="text-warning">■</span> mixed&ensp;
          <span className="text-danger">■</span> regret it
          <span className="block mt-1">
            Split reflects the {s.threads_read} reports read in full, not all{" "}
            {s.retrospective_count} found.
          </span>
        </p>
      </Card>

      <ListSection title="What people are glad about" items={s.common_positives} tone="success" />
      <ListSection title="Common regrets" items={s.common_regrets} tone="danger" />
      <ListSection title="Surprising findings" items={s.surprising_findings} />

      {s.sample_bias && (
        <Card className="p-5 bg-surface-2">
          <div className="eyebrow mb-2">Who actually posts updates</div>
          <p className="text-sm text-ink-muted">{s.sample_bias}</p>
        </Card>
      )}

      <Sources sources={s.sources} />
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
