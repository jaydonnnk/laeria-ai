"use client";

import { useState } from "react";
import { supabase } from "../../lib/supabase";

export default function Page() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function signIn(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    const { error } = await supabase.auth.signInWithPassword({ email, password });
    setBusy(false);
    if (error) {
      setError(error.message);
      return;
    }
    window.location.href = "/";
  }

  return (
    <main style={{ maxWidth: 380, margin: "120px auto", padding: "0 24px" }}>
      <h1>baryon.ai</h1>
      <p style={{ color: "#555" }}>Sign in with your owner account.</p>
      <form onSubmit={signIn} style={{ display: "grid", gap: 10 }}>
        <input
          type="email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          placeholder="Email"
          style={inputStyle}
          required
        />
        <input
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          placeholder="Password"
          style={inputStyle}
          required
        />
        {error && <p style={{ color: "#cf222e", margin: 0 }}>{error}</p>}
        <button type="submit" disabled={busy} style={buttonStyle}>
          {busy ? "Signing in…" : "Sign in"}
        </button>
      </form>
    </main>
  );
}

const inputStyle: React.CSSProperties = {
  padding: "10px 12px",
  fontSize: 15,
  border: "1px solid #ccc",
  borderRadius: 8,
  fontFamily: "inherit",
};

const buttonStyle: React.CSSProperties = {
  padding: "10px 16px",
  fontSize: 15,
  fontWeight: 600,
  borderRadius: 8,
  border: "none",
  background: "#1f2328",
  color: "#fff",
  cursor: "pointer",
};
