import { SourceThread } from "../lib/api";

/** The threads the agent actually read — rendered as verifiable citations. */
export function Sources({ sources }: { sources: SourceThread[] }) {
  if (!sources?.length) return null;
  return (
    <div
      style={{
        border: "1px solid #e1e4e8",
        borderRadius: 10,
        padding: "16px 20px",
      }}
    >
      <h3 style={{ marginTop: 0 }}>Threads the agent read</h3>
      <ol style={{ margin: 0, paddingLeft: 20, display: "grid", gap: 8 }}>
        {sources.map((s) => (
          <li key={s.id} style={{ lineHeight: 1.45 }}>
            <a
              href={s.url}
              target="_blank"
              rel="noopener noreferrer"
              style={{ color: "#0969da", textDecoration: "none" }}
            >
              {s.title || s.url}
            </a>
            <span style={{ color: "#888", fontSize: 13 }}>
              {" "}
              — r/{s.subreddit} · {s.score} pts · {s.num_comments} comments
            </span>
          </li>
        ))}
      </ol>
    </div>
  );
}
