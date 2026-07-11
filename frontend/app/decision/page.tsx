"use client";

import { useState } from "react";
import { api, ResearchBrief } from "../../lib/api";
import { Sources } from "../../components/Sources";

const CONFIDENCE_COLOR: Record<string, string> = {
  high: "#1a7f37",
  moderate: "#9a6700",
  low: "#cf222e",
};

export default function Page() {
  const [query, setQuery] = useState("");
  const [context, setContext] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [brief, setBrief] = useState<ResearchBrief | null>(null);

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
    <main style={{ maxWidth: 720, margin: "60px auto", padding: "0 24px" }}>
      <a href="/">&larr; back</a>
      <h1>Decision synthesis</h1>
      <p style={{ color: "#555" }}>
        Describe a purchase or decision. The agent reads real Reddit threads
        across relevant communities and returns the honest consensus — not
        marketing, not SEO spam.
      </p>

      <form onSubmit={run} style={{ display: "grid", gap: 12 }}>
        <textarea
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder='e.g. "best budget mechanical keyboard for programming under $100"'
          rows={2}
          style={inputStyle}
          required
          minLength={3}
        />
        <input
          value={context}
          onChange={(e) => setContext(e.target.value)}
          placeholder="Optional context — budget, use case, constraints"
          style={inputStyle}
        />
        <button type="submit" disabled={loading || !query.trim()} style={buttonStyle}>
          {loading ? "Researching…" : "Research"}
        </button>
      </form>

      {loading && (
        <p style={{ color: "#888", marginTop: 24 }}>
          Reading Reddit threads — this takes 30–90 seconds. The agent is
          identifying communities, pulling threads, and synthesising.
        </p>
      )}

      {error && (
        <p style={{ color: "#cf222e", marginTop: 24 }}>
          Research failed: {error}
        </p>
      )}

      {brief && <BriefCard brief={brief} />}
    </main>
  );
}

function BriefCard({ brief }: { brief: ResearchBrief }) {
  const sq = brief.signal_quality;
  return (
    <div style={{ marginTop: 32, display: "grid", gap: 20 }}>
      <div style={sectionStyle}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
          <h2 style={{ margin: 0 }}>Verdict</h2>
          <span
            style={{
              color: CONFIDENCE_COLOR[brief.confidence] ?? "#555",
              fontWeight: 600,
              textTransform: "uppercase",
              fontSize: 13,
            }}
          >
            {brief.confidence} confidence
          </span>
        </div>
        <p style={{ fontSize: 17, lineHeight: 1.5 }}>
          {brief.consensus_pick || "No clear community consensus found."}
        </p>
        <p style={{ color: "#888", fontSize: 13, margin: 0 }}>
          Based on {sq.thread_count} threads across{" "}
          {sq.subreddits_checked.map((s) => `r/${s}`).join(", ")}
          {sq.date_range ? ` · ${sq.date_range}` : ""}
        </p>
      </div>

      <ListSection title="What users praise" items={brief.strengths} accent="#1a7f37" />
      <ListSection title="Red flags" items={brief.red_flags} accent="#cf222e" />
      <ListSection title="Known failure modes" items={brief.failure_modes} />
      <ListSection title="What review sites miss" items={brief.what_reviewers_miss} />
      <ListSection title="Alternatives the community mentions" items={brief.alternatives} />

      {sq.bias_notes && (
        <div style={{ ...sectionStyle, background: "#fff8f0" }}>
          <h3 style={{ marginTop: 0 }}>Signal quality note</h3>
          <p style={{ margin: 0, color: "#555" }}>{sq.bias_notes}</p>
        </div>
      )}

      <Sources sources={brief.sources} />
    </div>
  );
}

function ListSection({
  title,
  items,
  accent,
}: {
  title: string;
  items: string[];
  accent?: string;
}) {
  if (!items?.length) return null;
  return (
    <div style={sectionStyle}>
      <h3 style={{ marginTop: 0, color: accent }}>{title}</h3>
      <ul style={{ margin: 0, paddingLeft: 20, display: "grid", gap: 6 }}>
        {items.map((item, i) => (
          <li key={i} style={{ lineHeight: 1.45 }}>
            {item}
          </li>
        ))}
      </ul>
    </div>
  );
}

const inputStyle: React.CSSProperties = {
  padding: "10px 12px",
  fontSize: 15,
  border: "1px solid #ccc",
  borderRadius: 8,
  fontFamily: "inherit",
};

const buttonStyle: React.CSSProperties = {
  padding: "10px 16px",
  fontSize: 15,
  fontWeight: 600,
  borderRadius: 8,
  border: "none",
  background: "#1f2328",
  color: "#fff",
  cursor: "pointer",
};

const sectionStyle: React.CSSProperties = {
  border: "1px solid #e1e4e8",
  borderRadius: 10,
  padding: "16px 20px",
};
