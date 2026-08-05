"use client";

import { Suspense, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { supabase } from "../../lib/supabase";
import { GoogleButton } from "../../components/GoogleButton";
import { Button } from "../../components/ui/Button";
import { Input, Field } from "../../components/ui/Input";
import { Banner } from "../../components/ui/Banner";

function LoginForm() {
  const params = useSearchParams();
  const next = params.get("next") || "/decision";
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
    window.location.href = next;
  }

  return (
    <main className="min-h-screen grid place-items-center px-6 bg-bg">
      <div className="w-full max-w-[380px]">
        <Link href="/" className="font-mono font-medium tracking-tight text-ink text-lg">
          laeria<span className="text-accent">.</span>
        </Link>
        <div className="mt-8 bg-surface border border-hairline rounded-[--radius-lg] shadow-sm p-7">
          <div className="eyebrow mb-2">Welcome back</div>
          <h1 className="text-xl font-semibold text-ink mb-6">Sign in</h1>

          <GoogleButton label="Continue with Google" next={next} />

          <div className="flex items-center gap-3 my-5">
            <span className="h-px flex-1 bg-hairline" />
            <span className="font-mono text-[10px] tracking-wide uppercase text-ink-subtle">
              or
            </span>
            <span className="h-px flex-1 bg-hairline" />
          </div>

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

          <p className="mt-6 text-center text-sm text-ink-muted">
            No account yet?{" "}
            <Link
              href={`/signup?next=${encodeURIComponent(next)}`}
              className="text-accent hover:text-accent-hover font-medium"
            >
              Sign up
            </Link>
          </p>
        </div>
        <p className="mt-4 text-center text-xs text-ink-subtle">
          Research modes are open to everyone. The payment rail runs on the
          owner&apos;s own wallet and stays with them.
        </p>
      </div>
    </main>
  );
}

export default function LoginPage() {
  // useSearchParams needs a Suspense boundary to prerender.
  return (
    <Suspense fallback={<main className="min-h-screen bg-bg" />}>
      <LoginForm />
    </Suspense>
  );
}
