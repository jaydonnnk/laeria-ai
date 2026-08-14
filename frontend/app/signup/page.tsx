"use client";

import { Suspense, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { supabase } from "../../lib/supabase";
import { GoogleButton } from "../../components/GoogleButton";
import { Button } from "../../components/ui/Button";
import { Input, Field } from "../../components/ui/Input";
import { Banner } from "../../components/ui/Banner";

function Signup() {
  // Landing CTAs pass ?next= so a visitor who clicked "Monitor" lands on
  // Monitor after signing up, not on a generic home page.
  const params = useSearchParams();
  const next = params.get("next") || "/decision";
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [checkInbox, setCheckInbox] = useState(false);
  const [busy, setBusy] = useState(false);

  async function signUp(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);

    const { data, error } = await supabase.auth.signUp({
      email,
      password,
      options: { emailRedirectTo: `${window.location.origin}/auth/callback?next=${encodeURIComponent(next)}` },
    });
    setBusy(false);

    if (error) {
      setError(error.message);
      return;
    }
    // With email confirmation on, Supabase returns a user but no session —
    // the account exists and is unusable until the link is clicked. Saying
    // "signed up!" and dumping them on an empty app would be a lie.
    if (data.session) {
      window.location.href = next;
      return;
    }
    setCheckInbox(true);
  }

  return (
    <main className="min-h-screen grid place-items-center px-6 bg-bg">
      <div className="w-full max-w-[380px]">
        <Link href="/" className="font-mono font-medium tracking-tight text-ink text-lg">
          laeria<span className="text-accent">.</span>
        </Link>

        <div className="mt-8 bg-surface border border-hairline rounded-[--radius-lg] shadow-sm p-7">
          <div className="eyebrow mb-2">Create an account</div>
          <h1 className="text-xl font-semibold text-ink mb-6">
            Ask what&apos;s worth it
          </h1>

          {checkInbox ? (
            <Banner tone="info">
              Check <b>{email}</b> for a confirmation link. Your account is
              created but can&apos;t be used until you click it.
            </Banner>
          ) : (
            <>
              <GoogleButton label="Sign up with Google" next={next} />

              <div className="flex items-center gap-3 my-5">
                <span className="h-px flex-1 bg-hairline" />
                <span className="font-mono text-[10px] tracking-wide uppercase text-ink-subtle">
                  or
                </span>
                <span className="h-px flex-1 bg-hairline" />
              </div>

              <form onSubmit={signUp} className="grid gap-4">
                <Field label="Email">
                  <Input
                    type="email"
                    value={email}
                    onChange={(e) => setEmail(e.target.value)}
                    placeholder="you@example.com"
                    autoComplete="email"
                    required
                  />
                </Field>
                <Field label="Password">
                  <Input
                    type="password"
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                    placeholder="at least 6 characters"
                    autoComplete="new-password"
                    minLength={6}
                    required
                  />
                </Field>
                {error && <Banner tone="error">{error}</Banner>}
                <Button type="submit" disabled={busy} className="w-full mt-1">
                  {busy ? "Creating account…" : "Create account"}
                </Button>
              </form>
            </>
          )}

          <p className="mt-6 text-center text-sm text-ink-muted">
            Already have an account?{" "}
            <Link
              href={`/login?next=${encodeURIComponent(next)}`}
              className="text-accent hover:text-accent-hover font-medium"
            >
              Sign in
            </Link>
          </p>
        </div>

        <p className="mt-4 text-center text-xs text-ink-subtle">
          Every account gets its own testnet agent wallet, funded automatically
          on your first visit to Commerce — nothing you set up by hand.
        </p>
      </div>
    </main>
  );
}

export default function SignupPage() {
  // useSearchParams needs a Suspense boundary to prerender.
  return (
    <Suspense fallback={<main className="min-h-screen bg-bg" />}>
      <Signup />
    </Suspense>
  );
}
