"use client";

import { useState } from "react";
import { SourceThread } from "../../lib/api";
import { Card } from "../ui/Card";

const PREVIEW = 3;

/** Upvote caret — the same shape the landing page uses for community signal. */
function Caret() {
  return (
    <svg viewBox="0 0 12 10" aria-hidden className="w-2.5 h-2 text-signal" fill="currentColor">
      <path d="M6 0 12 8.5H0z" />
    </svg>
  );
}

function ThreadRow({ source }: { source: SourceThread }) {
  return (
    <a
      href={source.url}
      target="_blank"
      rel="noopener noreferrer"
      className="group flex gap-3 bg-surface border border-hairline rounded-[--radius] p-3.5 transition-colors hover:border-hairline-strong hover:bg-surface-2"
    >
      {/* Vote column, mirroring how the source actually presents itself.
          min-width, not width: a fixed 2rem kept the columns aligned but
          clipped any score above 999, and popular threads are exactly the
          ones worth showing. It now aligns at the common case and grows for
          the rest. */}
      <div className="flex flex-col items-center gap-1 pt-0.5 shrink-0 min-w-[2.5rem]">
        <Caret />
        <span className="tnum text-[11px] text-ink-muted leading-none">
          {source.score.toLocaleString()}
        </span>
      </div>
      <div className="min-w-0">
        {/* A subreddit name can be long enough to need wrapping on a phone. */}
        <div className="font-mono text-[10px] tracking-wide text-ink-subtle mb-1 break-words">
          r/{source.subreddit} · {source.num_comments.toLocaleString()} comments
        </div>
        {/* break-words: real thread titles are long and must never push the
            card wider than the phone it is being read on. */}
        <div className="text-[13px] leading-snug text-ink break-words group-hover:text-accent transition-colors">
          {source.title || source.url}
        </div>
      </div>
    </a>
  );
}

/**
 * The threads the verdict was actually built from.
 *
 * This is the product's whole claim — "real people said this" — so a few real
 * threads appear right under the verdict instead of at the bottom of the page.
 * The rest stay one click away; nothing is hidden or removed.
 *
 * Titles, communities, scores and comment counts are exactly what the backend
 * returned. No quotes are shown, because the API does not return comment text
 * and inventing any would undo the point of the panel.
 */
export function EvidencePanel({ sources }: { sources: SourceThread[] }) {
  const [expanded, setExpanded] = useState(false);
  if (!sources?.length) return null;

  const shown = expanded ? sources : sources.slice(0, PREVIEW);
  const hidden = sources.length - shown.length;

  return (
    <Card className="p-5">
      <div className="flex items-baseline justify-between gap-3 mb-3">
        <h3 className="font-semibold text-ink">What people actually said</h3>
        <span className="eyebrow shrink-0">
          {sources.length} thread{sources.length === 1 ? "" : "s"}
        </span>
      </div>

      <div className="grid gap-2.5">
        {shown.map((s) => (
          <ThreadRow key={s.id} source={s} />
        ))}
      </div>

      {hidden > 0 && (
        <button
          onClick={() => setExpanded(true)}
          className="mt-3 text-sm font-medium text-accent hover:text-accent-hover transition-colors"
        >
          See all {sources.length} threads ↓
        </button>
      )}
      {expanded && sources.length > PREVIEW && (
        <button
          onClick={() => setExpanded(false)}
          className="mt-3 text-sm text-ink-muted hover:text-ink transition-colors"
        >
          Show fewer ↑
        </button>
      )}
    </Card>
  );
}
