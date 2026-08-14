"use client";

import { Card } from "../ui/Card";

// What the agent does across a whole run. These are a DESCRIPTION of the
// process, deliberately not a checklist: the backend reports elapsed time and
// nothing else, so the page cannot know which of these is happening now or
// which have finished. Every line is styled identically for that reason —
// no ticks, no highlight, no "step 2 of 5". Claiming progress we cannot
// observe would be the one dishonest thing on an otherwise honest screen.
const WHAT_IT_DOES = [
  "Finding the communities that would actually know",
  "Reading the discussions they have already had",
  "Weighing praise against complaints",
  "Checking how strong the evidence really is",
  "Writing the verdict",
];

/** A placeholder shaped like a Reddit thread: vote column, community, title. */
function ThreadSkeleton({ delay }: { delay: number }) {
  return (
    <div className="bg-surface border border-hairline rounded-[--radius] p-3.5 flex gap-3">
      <div className="flex flex-col items-center gap-1.5 pt-0.5 shrink-0">
        <div
          className="w-0 h-0 border-x-[5px] border-x-transparent border-b-[7px] border-b-signal/30 animate-pulse"
          style={{ animationDelay: `${delay}ms` }}
        />
        <div
          className="h-2 w-5 rounded-full bg-surface-2 animate-pulse"
          style={{ animationDelay: `${delay}ms` }}
        />
      </div>
      <div className="min-w-0 flex-1 grid gap-2">
        <div
          className="h-2 w-24 rounded-full bg-surface-2 animate-pulse"
          style={{ animationDelay: `${delay}ms` }}
        />
        <div
          className="h-2.5 w-full rounded-full bg-surface-2 animate-pulse"
          style={{ animationDelay: `${delay + 120}ms` }}
        />
        <div
          className="h-2.5 w-[70%] rounded-full bg-surface-2 animate-pulse"
          style={{ animationDelay: `${delay + 240}ms` }}
        />
      </div>
    </div>
  );
}

/**
 * The waiting screen for a research run.
 *
 * A run takes 30–90 seconds, which used to be one line of text and a counter —
 * long enough to read as broken. This makes the wait feel deliberate: pulsing
 * thread-shaped placeholders stand in for the discussions being read, beside a
 * plain description of the work.
 *
 * The elapsed timer is the only number on screen, and it is real.
 */
export function ResearchProgress({ seconds }: { seconds: number }) {
  return (
    <Card className="p-6 mb-6">
      <div className="flex items-center justify-between gap-3 mb-5">
        <div className="flex items-center gap-2.5 min-w-0">
          <span
            aria-hidden
            className="w-3.5 h-3.5 rounded-full border-2 border-hairline border-t-accent animate-spin shrink-0"
          />
          {/* Wraps rather than clips. The heading is short, but a clipped
              heading on the one screen the user stares at for a minute is
              the worst place to save a few pixels. */}
          <h2 className="text-[15px] sm:text-base font-semibold text-ink">
            Reading real discussions…
          </h2>
        </div>
        {/* tabular-width + shrink-0 so the number never nudges the heading as
            it ticks past 9s, 99s and 100s. */}
        <span
          className="tnum text-sm text-ink-subtle shrink-0 self-start pt-0.5"
          aria-live="polite"
        >
          {seconds.toFixed(0)}s
        </span>
      </div>

      <div className="grid gap-6 md:grid-cols-[1fr_1fr]">
        <div className="grid gap-2.5" aria-hidden>
          {[0, 260, 520].map((d) => (
            <ThreadSkeleton key={d} delay={d} />
          ))}
        </div>

        <div>
          <div className="eyebrow mb-3">What the agent does</div>
          <ul className="grid gap-2.5">
            {WHAT_IT_DOES.map((line) => (
              <li
                key={line}
                className="flex gap-2.5 text-sm leading-snug text-ink-muted"
              >
                <span className="mt-1.5 w-1.5 h-1.5 rounded-full shrink-0 bg-hairline-strong" />
                {line}
              </li>
            ))}
          </ul>
          <p className="text-[13px] text-ink-subtle mt-4">
            Reading real threads takes 30–90 seconds. You can leave this open.
          </p>
        </div>
      </div>
    </Card>
  );
}
