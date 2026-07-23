"use client";

import { useState } from "react";
import Link from "next/link";
import { supabase } from "../../lib/supabase";
import { Button } from "../../components/ui/Button";
import { Input, Field } from "../../components/ui/Input";
import { Banner } from "../../components/ui/Banner";

export default function LoginPage() {
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
    window.location.href = "/decision";
  }

  return (
    <main className="min-h-screen grid place-items-center px-6 bg-bg">
      <div className="w-full max-w-[380px]">
        <Link href="/" className="font-mono font-medium tracking-tight text-ink text-lg">
          laeria<span className="text-accent">.</span>
        </Link>
        <div className="mt-8 bg-surface border border-hairline rounded-[--radius-lg] shadow-sm p-7">
          <div className="eyebrow mb-2">Owner access</div>
          <h1 className="text-xl font-semibold text-ink mb-6">Sign in to the console</h1>
          <form onSubmit={signIn} className="grid gap-4">
            <Field label="Email">
              <Input
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="you@example.com"
                required
              />
            </Field>
            <Field label="Password">
              <Input
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="••••••••"
                required
              />
            </Field>
            {error && <Banner tone="error">{error}</Banner>}
            <Button type="submit" disabled={busy} className="w-full mt-1">
              {busy ? "Signing in…" : "Sign in"}
            </Button>
          </form>
        </div>
        <p className="mt-4 text-center text-xs text-ink-subtle font-mono">
          single-owner build · restricted access
        </p>
      </div>
    </main>
  );
}
