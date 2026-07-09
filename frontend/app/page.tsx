export default function Home() {
  return (
    <main style={{ maxWidth: 640, margin: "80px auto", padding: "0 24px" }}>
      <h1>reddit-signal</h1>
      <p style={{ color: "#555" }}>
        Reddit as a human-truth signal across the lifecycle of a decision.
        This is the Phase 0.1 scaffold — feature pages are stubs.
      </p>
      <ul>
        <li><a href="/decision">Decision synthesis (Mode 2)</a></li>
        <li><a href="/research">Retrospective mining (Mode 1)</a></li>
        <li><a href="/monitor">Monitoring (Mode 3)</a></li>
      </ul>
    </main>
  );
}
