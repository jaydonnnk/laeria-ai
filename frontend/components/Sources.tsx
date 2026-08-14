import { SourceThread } from "../lib/api";

/** The threads the agent actually read — rendered as verifiable citations. */
export function Sources({ sources }: { sources: SourceThread[] }) {
  if (!sources?.length) return null;
  return (
    <div className="bg-surface border border-hairline rounded-[--radius-lg] p-5">
      <div className="eyebrow mb-3">Threads the agent read</div>
      <ol className="grid gap-2.5">
        {sources.map((s, i) => (
          <li key={s.id} className="flex gap-3 text-sm leading-snug">
            <span className="tnum text-ink-subtle shrink-0">
              {String(i + 1).padStart(2, "0")}
            </span>
            {/* Title and details on separate lines: run together they wrapped
                into a ragged block on a phone, and long thread titles pushed
                the row wider than the screen. */}
            <span className="min-w-0">
              <a
                href={s.url}
                target="_blank"
                rel="noopener noreferrer"
                className="text-info hover:underline break-words"
              >
                {s.title || s.url}
              </a>
              <span className="block text-ink-subtle text-[13px] mt-0.5">
                r/{s.subreddit} · <span className="tnum">{s.score}</span> pts ·{" "}
                <span className="tnum">{s.num_comments}</span> comments
              </span>
            </span>
          </li>
        ))}
      </ol>
    </div>
  );
}
