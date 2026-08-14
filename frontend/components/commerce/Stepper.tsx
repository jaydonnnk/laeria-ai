"use client";

import { cn } from "../../lib/cn";

export type StepState = "done" | "active" | "todo";

export interface Step {
  key: string;
  label: string;
  caption: string;
  state: StepState;
}

/** The payment lifecycle rail: Fund -> Discover -> Issue -> Execute.
 *  Completed nodes fill jade; the active node pulses. */
export function Stepper({ steps }: { steps: Step[] }) {
  return (
    <ol className="flex items-stretch gap-0" role="list">
      {steps.map((step, i) => {
        const last = i === steps.length - 1;
        return (
          <li key={step.key} className="flex-1 flex flex-col gap-3">
            <div className="flex items-center">
              <Node index={i + 1} state={step.state} />
              {!last && (
                <span
                  className={cn(
                    "flex-1 h-px mx-2 transition-colors duration-500",
                    step.state === "done" ? "bg-accent" : "bg-hairline-strong"
                  )}
                />
              )}
            </div>
            <div className="pr-2 sm:pr-4">
              <div
                className={cn(
                  "text-[13px] sm:text-sm font-semibold transition-colors",
                  step.state === "todo" ? "text-ink-subtle" : "text-ink"
                )}
              >
                {step.label}
              </div>
              {/* The caption is the first thing to go on a narrow screen:
                  four of them across a phone wrapped into unreadable columns,
                  and the step names alone still tell the story. */}
              <div className="hidden sm:block text-xs text-ink-muted mt-0.5">
                {step.caption}
              </div>
            </div>
          </li>
        );
      })}
    </ol>
  );
}

function Node({ index, state }: { index: number; state: StepState }) {
  return (
    <span className="relative inline-flex">
      <span
        className={cn(
          "relative w-8 h-8 rounded-full grid place-items-center text-[13px] font-mono font-medium border transition-colors duration-300",
          state === "done" && "bg-accent text-accent-ink border-accent",
          state === "active" &&
            "bg-surface text-accent border-accent shadow-[0_0_0_3px_var(--color-accent-soft)]",
          state === "todo" && "bg-surface text-ink-subtle border-hairline-strong"
        )}
      >
        {state === "done" ? <Check /> : index}
      </span>
    </span>
  );
}

function Check() {
  return (
    <svg width="14" height="14" viewBox="0 0 16 16" fill="none" aria-hidden>
      <path
        d="M3.5 8.5l3 3 6-7"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}
