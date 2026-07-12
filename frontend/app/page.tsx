export default function Home() {
  return (
    <main style={{ maxWidth: 640, margin: "80px auto", padding: "0 24px" }}>
      <h1>baryon.ai</h1>
      <p style={{ color: "#555" }}>
        Reddit as a human-truth signal across the lifecycle of a decision —
        research it, learn how it turned out for others, watch it after you
        commit, and let the agent act within your mandate.
      </p>
      <ul>
        <li><a href="/decision">Decision synthesis (Mode 2)</a></li>
        <li><a href="/research">Retrospective mining (Mode 1)</a></li>
        <li><a href="/monitor">Monitoring (Mode 3)</a></li>
        <li><a href="/actions">Actions &amp; mandate (payments)</a></li>
      </ul>
    </main>
  );
}
