"use client";

import { useState } from "react";
import { api, OutcomeSummary } from "../../lib/api";
import { Sources } from "../../components/Sources";

const CONFIDENCE_COLOR: Record<string, string> = {
  high: "#1a7f37",
  moderate: "#9a6700",
  low: "#cf222e",
};

export default function Page() {
  const [decision, setDecision] = useState("");
  const [context, setContext] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [summary, setSummary] = useState<OutcomeSummary | null>(null);

  async function run(e: React.FormEvent) {
    e.preventDefault();
    if (!decision.trim() || loading) return;
    setLoading(true);
    setError(null);
    setSummary(null);
    try {
      setSummary(await api.startRetrospective(decision.trim(), context.trim()));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }

  return (
    <main style={{ maxWidth: 720, margin: "60px auto", padding: "0 24px" }}>
      <a href="/">&larr; back</a>
      <h1>Retrospective mining</h1>
      <p style={{ color: "#555" }}>
        Describe a decision you&apos;re about to make. The agent hunts update
        posts — real people who made the same call and came back to report how
        it went. Not opinions. Outcomes.
      </p>

      <form onSubmit={run} style={{ display: "grid", gap: 12 }}>
        <textarea
          value={decision}
          onChange={(e) => setDecision(e.target.value)}
          placeholder='e.g. "getting LASIK eye surgery" or "dropping out of uni to work full-time"'
          rows={2}
          style={inputStyle}
          required
          minLength={3}
        />
        <input
          value={context}
          onChange={(e) => setContext(e.target.value)}
          placeholder="Optional context about your situation"
          style={inputStyle}
        />
        <button type="submit" disabled={loading || !decision.trim()} style={buttonStyle}>
          {loading ? "Mining update posts…" : "Find outcomes"}
        </button>
      </form>

      {loading && (
        <p style={{ color: "#888", marginTop: 24 }}>
          Hunting retrospective posts across Reddit — this is the slowest
          research mode, expect 2–4 minutes. The agent is searching outcome
          phrasings, classifying which posts are genuine reports, and reading
          the strongest ones.
        </p>
      )}

      {error && (
        <p style={{ color: "#cf222e", marginTop: 24 }}>Research failed: {error}</p>
      )}

      {summary && <OutcomesCard s={summary} />}
    </main>
  );
}

function OutcomesCard({ s }: { s: OutcomeSummary }) {
  const pctFmt = (x: number) => `${Math.round(x * 100)}%`;
  return (
    <div style={{ marginTop: 32, display: "grid", gap: 20 }}>
      {s.thin_coverage && (
        <div style={{ ...sectionStyle, background: "#fff8f0", borderColor: "#d4a72c" }}>
          <strong>Limited data.</strong> Fewer than 5 genuine outcome reports
          were found for this decision. What follows is the honest best from a
          thin sample — treat it as anecdote, not consensus.
        </div>
      )}

      <div style={sectionStyle}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
          <h2 style={{ margin: 0 }}>
            Based on {s.retrospective_count} outcome report
            {s.retrospective_count === 1 ? "" : "s"}
          </h2>
          <span
            style={{
              color: CONFIDENCE_COLOR[s.confidence] ?? "#555",
              fontWeight: 600,
              textTransform: "uppercase",
              fontSize: 13,
            }}
          >
            {s.confidence} confidence
          </span>
        </div>

        <div style={{ display: "flex", gap: 0, marginTop: 16, borderRadius: 6, overflow: "hidden", height: 28 }}>
          {s.pct_positive > 0 && (
            <div style={{ ...barStyle, flex: s.pct_positive, background: "#1a7f37" }}>
              {pctFmt(s.pct_positive)}
            </div>
          )}
          {s.pct_mixed > 0 && (
            <div style={{ ...barStyle, flex: s.pct_mixed, background: "#9a6700" }}>
              {pctFmt(s.pct_mixed)}
            </div>
          )}
          {s.pct_negative > 0 && (
            <div style={{ ...barStyle, flex: s.pct_negative, background: "#cf222e" }}>
              {pctFmt(s.pct_negative)}
            </div>
          )}
        </div>
        <p style={{ color: "#888", fontSize: 13, marginBottom: 0 }}>
          <span style={{ color: "#1a7f37" }}>■</span> glad they did it&ensp;
          <span style={{ color: "#9a6700" }}>■</span> mixed&ensp;
          <span style={{ color: "#cf222e" }}>■</span> regret it
        </p>
      </div>

      <ListSection title="What people are glad about" items={s.common_positives} accent="#1a7f37" />
      <ListSection title="Common regrets" items={s.common_regrets} accent="#cf222e" />
      <ListSection title="Surprising findings" items={s.surprising_findings} />

      {s.sample_bias && (
        <div style={{ ...sectionStyle, background: "#f6f8fa" }}>
          <h3 style={{ marginTop: 0 }}>Who actually posts updates</h3>
          <p style={{ margin: 0, color: "#555" }}>{s.sample_bias}</p>
        </div>
      )}

      <Sources sources={s.sources} />
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

const barStyle: React.CSSProperties = {
  display: "flex",
  alignItems: "center",
  justifyContent: "center",
  color: "#fff",
  fontSize: 12,
  fontWeight: 600,
  minWidth: 0,
};
