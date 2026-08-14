"use client";

import { Card } from "../ui/Card";

/**
 * The bridge between "Worth it?" and buying.
 *
 * Arriving from a research result used to drop you at the top of a funding
 * panel, with the only mention of where you had come from sitting further
 * down the page — so the story broke exactly where it should have been
 * strongest. This card sits above everything and keeps the thread: what the
 * research picked, and what the agent is doing about it.
 *
 * Presentation only. The searching and buying are unchanged.
 */
export function ResearchHandoff({
  pick,
  status,
  product,
  note,
}: {
  /** The search text carried over from the research result. */
  pick: string;
  status: "searching" | "selected" | "none" | "browsing";
  product?: { title: string; price: number } | null;
  /** Fallback line when nothing matched. */
  note?: string | null;
}) {
  return (
    <Card className="p-5 mb-8 border-accent/30 bg-accent-soft">
      <div className="eyebrow mb-2">From your research</div>

      <p className="text-[15px] text-ink font-medium break-words">“{pick}”</p>

      <div className="mt-3 pt-3 border-t border-accent/20">
        {status === "searching" && (
          <div className="flex items-center gap-2.5 text-sm text-ink-muted">
            <span
              aria-hidden
              className="w-3.5 h-3.5 rounded-full border-2 border-accent/25 border-t-accent animate-spin shrink-0"
            />
            Finding the matching product in the store…
          </div>
        )}

        {status === "selected" && product && (
          <>
            {/* The name shrinks and wraps; the price never does. Without
                min-w-0 a long product name refuses to shrink and pushes the
                price off the edge of the card. */}
            <div className="flex items-baseline justify-between gap-3">
              <span className="font-semibold text-ink break-words min-w-0">
                {product.title}
              </span>
              <span className="tnum font-semibold text-ink shrink-0">
                ${product.price.toFixed(2)}
              </span>
            </div>
            <p className="text-[13px] text-ink-muted mt-1.5">
              The agent chose this and proposed the purchase. Your spending
              rules decide whether it goes through or waits for approval.
            </p>
          </>
        )}

        {status === "none" && (
          <p className="text-sm text-ink-muted">
            {note ?? "No available product matched the pick — search the store below."}
          </p>
        )}

        {status === "browsing" && (
          <p className="text-sm text-ink-muted">
            Showing store matches for the research pick. Nothing is bought until
            you choose it.
          </p>
        )}
      </div>
    </Card>
  );
}
