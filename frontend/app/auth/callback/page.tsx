"use client";

import { Suspense, useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import Link from "next/link";
import { supabase } from "../../../lib/supabase";

/** Where Google sends the user back to.
 *
 *  supabase-js uses PKCE by default, so the return trip carries a `code` that
 *  has to be exchanged for a session before anything is signed in. Older
 *  implicit-flow returns instead put tokens in the URL hash, which the client
 *  picks up on its own — both are handled, because which one applies depends
 *  on Supabase project settings rather than on this code.
 */
function Callback() {
  const router = useRouter();
  const params = useSearchParams();
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;

    (async () => {
      const next = params.get("next") || "/decision";

      // Google returns ?error=access_denied when the user cancels the consent
      // screen. That is not a failure worth showing a stack trace for.
      const denied = params.get("error_description") || params.get("error");
      if (denied) {
        if (!cancelled) setError(denied);
        return;
      }

      const code = params.get("code");
      if (code) {
        const { error } = await supabase.auth.exchangeCodeForSession(code);
        if (error) {
          if (!cancelled) setError(error.message);
          return;
        }
      }

      // Implicit-flow returns have already been consumed by the client here.
      const { data } = await supabase.auth.getSession();
      if (cancelled) return;
      if (!data.session) {
        setError("Signed in with Google, but no session came back. Check that this site's URL is on Supabase's redirect allow-list.");
        return;
      }
      router.replace(next);
    })();

    return () => {
      cancelled = true;
    };
  }, [params, router]);

  return (
    <main className="min-h-screen grid place-items-center px-6 bg-bg">
      <div className="w-full max-w-[420px] text-center">
        <Link href="/" className="font-mono font-medium tracking-tight text-ink text-lg">
          laeria<span className="text-accent">.</span>
        </Link>
        {error ? (
          <div className="mt-8 bg-surface border border-danger/30 rounded-[--radius-lg] p-6 text-left">
            <div className="eyebrow mb-2 text-danger">Sign-in failed</div>
            <p className="text-sm text-ink-muted">{error}</p>
            <Link
              href="/login"
              className="mt-4 inline-block text-sm font-medium text-accent hover:text-accent-hover"
            >
              Back to sign in →
            </Link>
          </div>
        ) : (
          <p className="mt-8 text-sm text-ink-muted">Signing you in…</p>
        )}
      </div>
    </main>
  );
}

export default function AuthCallbackPage() {
  // useSearchParams needs a Suspense boundary to prerender.
  return (
    <Suspense fallback={<main className="min-h-screen bg-bg" />}>
      <Callback />
    </Suspense>
  );
}
