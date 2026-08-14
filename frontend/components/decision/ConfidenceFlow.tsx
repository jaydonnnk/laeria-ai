"use client";

import { ConfidenceLevel } from "../../lib/api";

// Explicit ranking. The backend sends these as plain strings, so comparing
// them directly would sort alphabetically — "high" < "low" — and quietly
// invert every comparison below.
const RANK: Record<ConfidenceLevel, number> = { low: 0, moderate: 1, high: 2 };

// Tint per confidence level, matching the badge colours used elsewhere.
const LEVEL_TONE: Record<ConfidenceLevel, string> = {
  high: "bg-success-soft text-success border-success/20",
  moderate: "bg-warning-soft text-warning border-warning/20",
  low: "bg-danger-soft text-danger border-danger/20",
};

type CheckState = "passed" | "capped" | "no-pick";

const CHECK_TONE: Record<CheckState, string> = {
  passed: "bg-accent-soft text-accent border-accent/20",
  capped: "bg-warning-soft text-warning border-warning/20",
  "no-pick": "bg-surface-2 text-ink-muted border-hairline",
};

const CHECK_LABEL: Record<CheckState, string> = {
  passed: "Passed",
  capped: "Capped",
  "no-pick": "No pick",
};

/** One box in the chain. */
function Node({
  label,
  value,
  tone,
  strong,
}: {
  label: string;
  value: string;
  tone: string;
  strong?: boolean;
}) {
  return (
    <div
      className={`flex-1 min-w-0 border rounded-[--radius] px-3.5 py-3 sm:px-4 ${tone}`}
    >
      {/* The label may wrap to two lines in a narrow column; items-stretch on
          the parent keeps all three boxes the same height when it does. */}
      <div className="eyebrow mb-1.5 leading-[1.4]">{label}</div>
      <div
        className={`font-medium capitalize leading-tight break-words ${
          strong ? "text-[19px]" : "text-[15px]"
        }`}
      >
        {value}
      </div>
    </div>
  );
}

/** The arrow between two boxes — sideways on wide screens, down on narrow. */
function Arrow() {
  return (
    <div
      aria-hidden
      className="flex items-center justify-center text-ink-subtle shrink-0 leading-none px-2"
    >
      <span className="sm:hidden">↓</span>
      <span className="hidden sm:inline">→</span>
    </div>
  );
}

/**
 * How the final confidence was reached, as a picture rather than a paragraph.
 *
 *   AI judgement  →  Evidence check  →  Final
 *
 * The point it has to land in about one second: the model gives an opinion,
 * and laeria separately checks whether the evidence is strong enough to
 * support it. The check can only ever lower the verdict.
 *
 * Every value comes from the backend. Nothing here is a score, a percentage,
 * or a guess — the three boxes are the three fields the API already returns.
 */
export function ConfidenceFlow({
  semantic,
  ceiling,
  final,
  hasPick,
}: {
  semantic: ConfidenceLevel;
  ceiling: ConfidenceLevel;
  final: ConfidenceLevel;
  hasPick: boolean;
}) {
  // A brief with no recommendation is LOW whatever the two layers concluded,
  // so it gets its own middle state rather than being described as "capped"
  // by an evidence rule that may never have fired.
  const check: CheckState = !hasPick
    ? "no-pick"
    : RANK[ceiling] < RANK[semantic]
      ? "capped"
      : "passed";

  return (
    <div>
      <div className="flex flex-col sm:flex-row sm:items-stretch gap-2 sm:gap-0">
        <Node label="AI judgement" value={semantic} tone="bg-surface-2 text-ink border-hairline" />
        <Arrow />
        <Node label="Evidence check" value={CHECK_LABEL[check]} tone={CHECK_TONE[check]} />
        <Arrow />
        <Node label="Final verdict" value={final} tone={LEVEL_TONE[final]} strong />
      </div>
      <p className="text-[13px] leading-relaxed text-ink-muted mt-4">
        {check === "capped" && (
          <>
            The AI proposed <b className="capitalize">{semantic}</b>. The evidence
            was not strong enough to support that, so laeria lowered the verdict
            to <b className="capitalize">{final}</b>.
          </>
        )}
        {check === "passed" && RANK[semantic] < RANK[ceiling] && (
          <>
            The evidence could have supported more, but the AI itself was more
            cautious — so the verdict stays at{" "}
            <b className="capitalize">{final}</b>.
          </>
        )}
        {check === "passed" && RANK[semantic] >= RANK[ceiling] && (
          <>
            The evidence was strong enough to support what the AI proposed, so
            nothing was lowered.
          </>
        )}
        {check === "no-pick" && (
          <>
            The threads did not produce a clear recommendation, so the verdict is
            low confidence no matter what either layer concluded.
          </>
        )}
      </p>
    </div>
  );
}
