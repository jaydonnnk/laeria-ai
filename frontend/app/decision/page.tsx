"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import {
  api,
  ConfidenceLevel,
  EvidenceState,
  ResearchBrief,
  SignalQuality,
} from "../../lib/api";
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

// Explicit ranking. The backend enum is a string enum, so comparing these
// values directly would order them alphabetically — "high" < "low" — and
// silently invert every comparison below.
const RANK: Record<ConfidenceLevel, number> = { low: 0, moderate: 1, high: 2 };

// What the primary message says when there is no verdict to report. Keyed on
// the backend's own state so the page never infers a cause from prose.
const EVIDENCE_STATE_COPY: Record<
  Exclude<EvidenceState, "ok">,
  { headline: string; detail: string }
> = {
  not_in_corpus: {
    headline: "This question isn't in the frozen Reddit demo corpus yet.",
    detail:
      "Logged-out Reddit access is blocked upstream, so only previously recorded questions can be answered right now. Nothing is wrong with your question.",
  },
  source_unavailable: {
    headline: "Reddit is currently unavailable.",
    detail:
      "This is a source-access limitation, not a negative verdict on your question. Recorded questions still work; live ones cannot be read until access is restored.",
  },
  no_evidence: {
    headline: "No relevant community evidence was found for this question.",
    detail:
      "The communities were reachable and were searched, but nothing matching came back. Rephrasing with the words people would actually use in a thread title often helps.",
  },
  no_usable_evidence: {
    // Deliberately does NOT blame filtering. The backend only reaches this
    // state when the full-thread fetches produced nothing — the filters
    // relax until something survives and never empty a non-empty corpus.
    headline:
      "Search results were found, but no full thread could be retrieved to read.",
    detail:
      "The agent located candidate discussions but could not fetch any of them in full, so there was nothing to synthesise a recommendation from.",
  },
};

// Shown when a backend sends a state this build does not know about — a newer
// API against an older page. Generic, never a crash.
const UNKNOWN_EVIDENCE_COPY = {
  headline: "No verdict could be produced for this question.",
  detail:
    "The agent did not return usable community evidence. This is a limitation of the research run, not a negative verdict on your question.",
};

/**
 * Read a brief tolerantly.
 *
 * The frontend and backend deploy independently, so for the length of a
 * rolling deploy this page can receive a brief written by the PREVIOUS
 * backend, which has no `evidence_state`, no `subreddits_represented` and no
 * confidence breakdown. TypeScript types describe the canonical API and are
 * erased at runtime — `undefined !== "ok"` is true, which would send an
 * ordinary brief down the no-verdict path and then crash on a missing copy
 * entry.
 *
 * `Partial<>` rather than `any`: the fields are genuinely absent on the old
 * wire shape, and every fallback below is the honest reading of "this backend
 * did not tell us". Remove once both sides are known to be deployed.
 */
function readBrief(brief: ResearchBrief) {
  const b: Partial<ResearchBrief> = brief;
  const sq: Partial<SignalQuality> = brief.signal_quality ?? {};
  // An old backend reported only the planned communities, so that is the best
  // available answer to "where did this come from" — no better one exists.
  const represented = sq.subreddits_represented ?? sq.subreddits_checked ?? [];
  const confidence = b.confidence ?? "low";
  // Did this response actually go through the structural policy?
  //
  // Tested on the breakdown fields themselves, never on `confidence` — every
  // brief ever returned has a confidence, including ones decided by the model
  // alone. Without this flag the fallbacks below (semantic = ceiling =
  // confidence) make an uncalibrated HIGH brief render as "nothing in the
  // evidence structure limited this verdict", which is not something the old
  // backend was in any position to know.
  const calibrationAvailable =
    b.semantic_confidence !== undefined && b.structural_ceiling !== undefined;
  return {
    confidence,
    calibrationAvailable,
    // Only meaningful when calibrationAvailable; present so the headline
    // helper has total values to work with either way.
    semantic: b.semantic_confidence ?? confidence,
    ceiling: b.structural_ceiling ?? confidence,
    reasons: b.confidence_reasons ?? [],
    consensusPick: b.consensus_pick ?? "",
    evidenceState: sq.evidence_state ?? "ok",
    checked: sq.subreddits_checked ?? [],
    represented,
    usableThreads: sq.usable_thread_count ?? sq.thread_count ?? 0,
    strongThreads: sq.strong_thread_count ?? 0,
    filtersRelaxed: sq.filters_relaxed ?? false,
    dateRange: sq.date_range ?? "",
    biasNotes: sq.bias_notes ?? "",
  };
}

/**
 * One true sentence about why the FINAL verdict is what it is.
 *
 * Order matters. There are FOUR inputs to the final value, not three: the
 * model's judgement, the structural ceiling, their minimum — and the separate
 * no-pick safety rule, which forces LOW independently of the other two.
 *
 * That fourth input is why the no-pick case has to be tested FIRST. A brief
 * can legitimately carry semantic=HIGH, ceiling=HIGH and final=LOW when one
 * half of the split synthesis fails: comparing only semantic against ceiling
 * would find them equal and cheerfully report that both layers "agree on low
 * confidence", when in fact both said HIGH and something else overrode them.
 *
 * Derived from the confidence fields alone. Never parsed out of the reason
 * strings, which are prose and may change wording.
 */
function confidenceHeadline(v: {
  confidence: ConfidenceLevel;
  semantic: ConfidenceLevel;
  ceiling: ConfidenceLevel;
  consensusPick: string;
  calibrationAvailable: boolean;
}): string {
  if (!v.calibrationAvailable) {
    // Deployment mismatch, not a product state. Saying anything about what
    // the structural layer concluded would be inventing it.
    return "This result came from a backend version that does not expose the structural confidence breakdown, so no calibrated explanation is available for it. Refresh after the backend update to see evidence reasons.";
  }
  if (!v.consensusPick.trim()) {
    return "No consensus pick was produced, so the verdict is low confidence regardless of what the analysis or the evidence structure supported.";
  }
  if (RANK[v.ceiling] < RANK[v.semantic]) {
    return `The analysis proposed ${v.semantic} confidence, but the evidence structure caps this verdict at ${v.ceiling}.`;
  }
  if (RANK[v.semantic] < RANK[v.ceiling]) {
    return `The evidence structure allowed up to ${v.ceiling} confidence; the analysis itself was more cautious.`;
  }
  if (v.confidence === "high") {
    return "Nothing in the evidence structure limited this verdict.";
  }
  return `The analysis and the evidence structure agree on ${v.confidence} confidence.`;
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
  // The query/context the displayed brief was actually researched from. The
  // form inputs can be edited after a result lands, and the server looks its
  // brief up by the exact researched string — acting on whatever happens to be
  // in the box would look up a brief that was never run.
  const [researched, setResearched] = useState<{ query: string; context: string } | null>(
    null
  );

  async function executePurchase() {
    if (!brief || !researched || acting) return;
    setActing(true);
    setActOutcome(null);
    try {
      // Only the identity of the research run is sent. The confidence and the
      // pick come from the brief the server itself stored.
      const res = await api.actOnBrief({
        query: researched.query,
        context: researched.context,
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
    setResearched(null);
    setElapsed(0);
    try {
      setBrief(await api.startDecision(q.trim(), ctx.trim(), 8, setElapsed));
      setResearched({ query: q.trim(), context: ctx.trim() });
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
          <Button type="submit" disabled={loading || !query.trim()} className="w-fit">
            {loading ? "Researching…" : "Research"}
          </Button>
        </form>
      </Card>

      {loading && (
        <Banner tone="info" className="mb-6">
          Reading Reddit threads — identifying communities, pulling threads,
          synthesising.
          <span className="tnum ml-2 text-ink-subtle">{elapsed.toFixed(0)}s</span>
        </Banner>
      )}
      {error && <Banner tone="error" className="mb-6">Research failed: {error}</Banner>}

      {brief && <BriefCard brief={brief} />}

      {/* Autonomous action requires a calibrated verdict. During a rolling
          deploy an old backend returns no structural breakdown, so there is no
          way to know whether its confidence would have survived the policy —
          fail closed and show the research result without the spend affordance
          rather than acting on an unverified verdict. */}
      {brief && brief.consensus_pick && brief.confidence !== "low" &&
        readBrief(brief).calibrationAvailable && (
        <Card className="mt-6 p-6 border-accent/30 bg-accent-soft">
          <h3 className="font-semibold text-ink mb-1">
            {brief.confidence === "high"
              ? "Consensus is strong — if it’s something you can buy, the agent can"
              : "The community leans this way, with caveats — the agent can still buy it"}
          </h3>
          {brief.confidence === "moderate" && (
            <p className="text-sm text-ink-muted mb-2">
              This is not a strong consensus. The evidence supports the pick but
              did not clear the bar for high confidence — see &ldquo;Why this
              confidence&rdquo; above before letting the agent spend.
            </p>
          )}
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

/**
 * There is no verdict to show — say which of the several possible reasons it
 * is. Rendering these as an ordinary red "low confidence" recommendation is
 * what made an upstream outage look identical to the community saying no.
 */
function NoVerdictCard({ brief }: { brief: ResearchBrief }) {
  const v = readBrief(brief);
  // An unrecognised state falls back to generic copy rather than crashing on
  // a missing map entry.
  const copy =
    EVIDENCE_STATE_COPY[v.evidenceState as Exclude<EvidenceState, "ok">] ??
    UNKNOWN_EVIDENCE_COPY;
  return (
    <div className="grid gap-4">
      <Card className="p-6">
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-lg font-semibold">No verdict</h2>
          <Badge tone="neutral">no evidence</Badge>
        </div>
        <p className="text-[17px] leading-relaxed text-ink">{copy.headline}</p>
        <p className="text-sm text-ink-muted mt-2">{copy.detail}</p>
        <p className="text-[13px] text-ink-subtle mt-3">
          Confidence is reported as{" "}
          <span className="font-medium">{v.confidence}</span> because there is
          nothing to weigh — not because the community was negative.
        </p>
      </Card>

      {v.biasNotes && (
        <Card className="p-5">
          <div className="eyebrow mb-2">What the agent tried</div>
          <p className="text-sm text-ink-muted">{v.biasNotes}</p>
          {v.checked.length > 0 && (
            <p className="text-[13px] text-ink-subtle mt-2">
              Searched {v.checked.map((s) => `r/${s}`).join(", ")}.
            </p>
          )}
        </Card>
      )}
    </div>
  );
}

/**
 * Why the verdict carries the confidence it does.
 *
 * Every sentence here is assembled from backend fields — the three confidence
 * values and the backend-authored reason list. The page never invents a cause.
 */
function WhyThisConfidence({ brief }: { brief: ResearchBrief }) {
  const v = readBrief(brief);
  return (
    <Card className="p-5">
      <div className="eyebrow mb-2">Why this confidence</div>
      <p className="text-sm text-ink">{confidenceHeadline(v)}</p>
      {v.reasons.length > 0 && (
        <ul className="grid gap-2 mt-3">
          {v.reasons.map((reason, i) => (
            <li key={i} className="flex gap-2.5 text-sm leading-snug text-ink-muted">
              <span className="mt-1.5 w-1.5 h-1.5 rounded-full shrink-0 bg-ink-subtle" />
              {reason}
            </li>
          ))}
        </ul>
      )}
      {v.calibrationAvailable && (
        <p className="text-[13px] text-ink-subtle mt-3">
          The model judges whether people agree; deterministic rules decide how
          much confidence that evidence is allowed to earn. Rules can only lower
          a verdict, never raise one.
        </p>
      )}
    </Card>
  );
}

function BriefCard({ brief }: { brief: ResearchBrief }) {
  const v = readBrief(brief);
  if (v.evidenceState !== "ok") return <NoVerdictCard brief={brief} />;

  const unsearched = v.checked.length - v.represented.length;
  return (
    <div className="grid gap-4">
      <Card className="p-6">
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-lg font-semibold">Verdict</h2>
          <Badge status={v.confidence}>{v.confidence} confidence</Badge>
        </div>
        <p className="text-[17px] leading-relaxed text-ink">
          {v.consensusPick || "No clear community consensus found."}
        </p>
        {/* Communities that actually contributed threads — never the ones the
            planner merely searched. The source list below is the receipt. */}
        <p className="text-[13px] text-ink-subtle mt-2">
          Based on <span className="tnum">{v.usableThreads}</span> threads across{" "}
          {v.represented.map((s) => `r/${s}`).join(", ") || "no community"}
          {v.dateRange ? ` · ${v.dateRange}` : ""}
          {unsearched > 0 && (
            <>
              {" "}· searched <span className="tnum">{v.checked.length}</span>{" "}
              communities in total
            </>
          )}
        </p>
        {/* Says only what is provable: how many threads cleared the bar. NOT
            that weaker threads were admitted — four strong threads and no weak
            ones still relaxes, because the count fell short of the target. */}
        {v.filtersRelaxed && (
          <p className="text-[13px] text-ink-subtle mt-1">
            Only <span className="tnum">{v.strongThreads}</span>{" "}
            {v.strongThreads === 1 ? "thread" : "threads"} cleared the
            strong-evidence bar — below the threshold for relying on strong
            evidence alone.
          </p>
        )}
      </Card>

      <WhyThisConfidence brief={brief} />

      <ListSection title="What users praise" items={brief.strengths} tone="success" />
      <ListSection title="Red flags" items={brief.red_flags} tone="danger" />
      <ListSection title="Known failure modes" items={brief.failure_modes} />
      <ListSection title="What review sites miss" items={brief.what_reviewers_miss} />
      <ListSection title="Alternatives the community mentions" items={brief.alternatives} />

      {v.biasNotes && (
        <Card className="p-5 bg-warning-soft border-warning/20">
          <div className="eyebrow mb-2">Signal quality note</div>
          <p className="text-sm text-ink-muted">{v.biasNotes}</p>
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
