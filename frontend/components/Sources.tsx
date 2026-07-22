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
            <span>
              <a
                href={s.url}
                target="_blank"
                rel="noopener noreferrer"
                className="text-info hover:underline"
              >
                {s.title || s.url}
              </a>
              <span className="text-ink-subtle">
                {" "}— r/{s.subreddit} · <span className="tnum">{s.score}</span> pts ·{" "}
                <span className="tnum">{s.num_comments}</span> comments
              </span>
            </span>
          </li>
        ))}
      </ol>
    </div>
  );
}
