"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { api, DiscoveredThread, ResearchBrief } from "../../lib/api";
import { Sources } from "../../components/Sources";
import { Button } from "../../components/ui/Button";
import { Card } from "../../components/ui/Card";
import { Badge } from "../../components/ui/Badge";
import { Banner } from "../../components/ui/Banner";
import { Input, Textarea, Field } from "../../components/ui/Input";

/** Plain English for how completely each discussion was held. Mirrors the
 *  backend's Acquisition levels; an unknown level is shown as-is rather than
 *  hidden, because silently dropping provenance is the failure this prevents. */
const ACQUISITION_LABELS: Record<string, [string, string]> = {
  full_browser: ["full discussion", "full discussions"],
  recorded_full: ["recorded discussion", "recorded discussions"],
  search_preview: ["search preview", "search previews"],
  reddit_oauth: ["full discussion", "full discussions"],
};

function provenanceLine(acquisition: Record<string, number>): string {
  const parts = Object.entries(acquisition)
    .filter(([, n]) => n > 0)
    .map(([level, n]) => {
      const [one, many] = ACQUISITION_LABELS[level] ?? [level, level];
      return `${n} ${n === 1 ? one : many}`;
    });
  return parts.length ? `Analysed ${parts.join(" and ")}.` : "";
}

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
  const [elapsed, setElapsed] = useState(0);
  const [actOutcome, setActOutcome] = useState<string | null>(null);
  const [acting, setActing] = useState(false);
  // Live community discovery: which discussions exist, and which the user
  // wants weighed. Separate from `brief` because choosing the evidence comes
  // before producing a verdict from it.
  const [found, setFound] = useState<DiscoveredThread[] | null>(null);
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [discovering, setDiscovering] = useState(false);

  async function discover(q: string) {
    if (!q.trim() || discovering) return;
    setDiscovering(true);
    setError(null);
    setBrief(null);
    setFound(null);
    try {
      const res = await api.discover(q.trim());
      setFound(res.results);
      setPicked(new Set(res.results.filter((r) => r.preselected).map((r) => r.thread_id)));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setDiscovering(false);
    }
  }

  async function analyseSelected() {
    if (!found || loading) return;
    const chosen = found.filter((t) => picked.has(t.thread_id));
    if (!chosen.length) return;
    setLoading(true);
    setError(null);
    setBrief(null);
    setElapsed(0);
    try {
      setBrief(await api.analyseSelected(query.trim(), chosen, setElapsed));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }

  function togglePick(id: string) {
    setPicked((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

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

  async function runQuery(q: string, ctx: string) {
    if (!q.trim() || loading) return;
    setLoading(true);
    setError(null);
    setBrief(null);
    setElapsed(0);
    try {
      setBrief(await api.startDecision(q.trim(), ctx.trim(), 8, setElapsed));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }

  async function run(e: React.FormEvent) {
    e.preventDefault();
    await runQuery(query, context);
  }

  // ?q= arrives from the browser extension's "full report" link. Read from
  // window rather than useSearchParams so this page keeps prerendering without
  // a Suspense boundary.
  //
  // It runs the query rather than only filling the box: the extension has
  // already researched this exact string, and the backend caches briefs for
  // 24h keyed on the normalised query — so this is a cache hit that renders
  // in about a second. Showing a pre-filled form and waiting for a second
  // click would be friction in front of an answer that already exists.
  const autoRan = useRef(false);
  useEffect(() => {
    if (autoRan.current) return;
    const q = new URLSearchParams(window.location.search).get("q");
    if (!q?.trim()) return;
    autoRan.current = true;
    setQuery(q);
    void runQuery(q, "");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <main className="max-w-[760px] mx-auto px-6 py-10 md:py-14">
      <div className="mb-8">
        <div className="eyebrow mb-3">Before you commit</div>
        <h1 className="text-2xl md:text-[2rem] font-semibold tracking-[-0.02em]">
          Worth it?
        </h1>
        <p className="mt-3 text-ink-muted">
          Describe something you&apos;re weighing up — a purchase, a procedure, a
          job, a move. The agent reads real Reddit threads across the
          communities that would know, and returns the honest consensus. Not
          marketing, not SEO spam.
        </p>
      </div>

      <Card className="p-6 mb-8">
        <form onSubmit={run} className="grid gap-4">
          <Field label="What are you deciding?">
            <Textarea
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder={
                'e.g. "best budget mechanical keyboard under $100" · ' +
                '"is LASIK worth it" · "should I do a bootcamp"'
              }
              rows={2}
              required
              minLength={3}
            />
          </Field>
          <Field label="Context (optional)">
            <Input
              value={context}
              onChange={(e) => setContext(e.target.value)}
              placeholder="Budget, constraints, your situation"
            />
          </Field>
          <div className="flex flex-wrap gap-3">
            <Button type="submit" disabled={loading || !query.trim()} className="w-fit">
              {loading ? "Researching…" : "Research"}
            </Button>
            {/* Live discovery: find current discussions and let the user pick
                which ones are weighed, before anything expensive runs. */}
            <Button
              type="button"
              variant="ghost"
              onClick={() => discover(query)}
              disabled={discovering || !query.trim()}
              className="w-fit"
            >
              {discovering ? "Finding discussions…" : "Find live discussions"}
            </Button>
          </div>
        </form>
      </Card>

      {found && (
        <Card className="p-6 mb-8">
          <div className="flex items-baseline justify-between mb-4">
            <h2 className="text-lg">Live community evidence</h2>
            <span className="text-sm text-ink-subtle">
              {found.length} discussion{found.length === 1 ? "" : "s"} found
            </span>
          </div>

          {found.length === 0 ? (
            <p className="text-sm text-ink-subtle">
              No current discussions were found for this question.
            </p>
          ) : (
            <>
              <ul className="grid gap-3 mb-5">
                {found.map((t) => (
                  <li key={t.thread_id} className="flex gap-3 items-start">
                    <input
                      type="checkbox"
                      id={`t-${t.thread_id}`}
                      checked={picked.has(t.thread_id)}
                      onChange={() => togglePick(t.thread_id)}
                      className="mt-1"
                    />
                    <label htmlFor={`t-${t.thread_id}`} className="grid gap-1 cursor-pointer">
                      <span className="leading-snug">{t.title}</span>
                      <span className="text-xs text-ink-subtle">
                        {t.subreddit ? `r/${t.subreddit}` : "reddit"}
                        {" · "}
                        <a
                          href={t.url}
                          target="_blank"
                          rel="noreferrer"
                          className="underline"
                          onClick={(e) => e.stopPropagation()}
                        >
                          Open Reddit ↗
                        </a>
                      </span>
                      {t.snippet && (
                        <span className="text-sm text-ink-subtle line-clamp-2">{t.snippet}</span>
                      )}
                    </label>
                  </li>
                ))}
              </ul>

              {/* Say plainly what will actually be read. A snippet is not a
                  conversation, and the extension is how it becomes one. */}
              <Banner tone="info" className="mb-4">
                These are search previews. Laeria cannot open Reddit itself — open a
                discussion and use the browser extension&apos;s{" "}
                <strong>Send this discussion to Laeria</strong> to have the full
                conversation analysed instead of the preview.
              </Banner>

              <Button
                type="button"
                onClick={analyseSelected}
                disabled={loading || picked.size === 0}
                className="w-fit"
              >
                {loading
                  ? "Analysing…"
                  : `Analyse ${picked.size} discussion${picked.size === 1 ? "" : "s"}`}
              </Button>
            </>
          )}
        </Card>
      )}

      {loading && (
        <Banner tone="info" className="mb-6">
          Reading Reddit threads — identifying communities, pulling threads,
          synthesising.
          <span className="tnum ml-2 text-ink-subtle">{elapsed.toFixed(0)}s</span>
        </Banner>
      )}
      {error && <Banner tone="error" className="mb-6">Research failed: {error}</Banner>}

      {brief && <BriefCard brief={brief} />}

      {brief && brief.consensus_pick && brief.confidence !== "low" && (
        <Card className="mt-6 p-6 border-accent/30 bg-accent-soft">
          <h3 className="font-semibold text-ink mb-1">
            Consensus is strong — if it&apos;s something you can buy, the agent can
          </h3>
          <p className="text-sm text-ink-muted mb-4">
            Only applies when the pick is a product. The agent finds it on the
            store, selects the best available match, mints a single-use card
            capped to the price, and checks out — all under your mandate.
            Anything above your confirm threshold waits for your approval
            instead of executing.
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
        {/* Say what was actually read. A search preview is a title and a
            snippet, not a conversation, and a reader who assumes otherwise has
            been misled by omission. */}
        {sq.acquisition && Object.keys(sq.acquisition).length > 0 && (
          <p className="text-[13px] text-ink-subtle mt-1">{provenanceLine(sq.acquisition)}</p>
        )}
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
