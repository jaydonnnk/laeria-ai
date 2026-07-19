"use client";

import { useEffect, useState } from "react";
import { supabase } from "../lib/supabase";

export default function Home() {
  const [email, setEmail] = useState<string | null>(null);

  useEffect(() => {
    supabase.auth.getSession().then(({ data }) => {
      setEmail(data.session?.user?.email ?? null);
    });
  }, []);

  async function signOut() {
    await supabase.auth.signOut();
    window.location.href = "/login";
  }

  return (
    <main style={{ maxWidth: 640, margin: "80px auto", padding: "0 24px" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
        <h1>baryon.ai</h1>
        {email ? (
          <span style={{ fontSize: 13, color: "#888" }}>
            {email}{" "}
            <button
              onClick={signOut}
              style={{
                marginLeft: 8,
                padding: "4px 10px",
                fontSize: 13,
                borderRadius: 6,
                border: "1px solid #ccc",
                background: "#fff",
                cursor: "pointer",
              }}
            >
              Sign out
            </button>
          </span>
        ) : (
          <a href="/login" style={{ fontSize: 14 }}>Sign in</a>
        )}
      </div>
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
        <li><a href="/commerce">Commerce (agent shopping)</a></li>
      </ul>
    </main>
  );
}
