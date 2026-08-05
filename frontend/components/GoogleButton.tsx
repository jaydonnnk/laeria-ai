"use client";

import { useState } from "react";
import { supabase } from "../lib/supabase";

/** Google OAuth entry point.
 *
 *  Sign-up and sign-in are the same call: Supabase creates the account on
 *  first use and signs in on every subsequent one, so both pages mount this
 *  and only the label differs.
 *
 *  redirectTo must be computed from the live origin rather than hardcoded, or
 *  a production login bounces to localhost. The origin also has to be on
 *  Supabase's redirect allow-list, which is configured in their dashboard —
 *  without that the round trip fails after Google, not before, which looks
 *  like the button is broken.
 */
export function GoogleButton({
  label = "Continue with Google",
  next = "/decision",
}: {
  label?: string;
  next?: string;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function go() {
    setBusy(true);
    setError(null);
    const { error } = await supabase.auth.signInWithOAuth({
      provider: "google",
      options: { redirectTo: `${window.location.origin}/auth/callback?next=${encodeURIComponent(next)}` },
    });
    // On success the browser navigates away, so reaching here means it failed.
    if (error) {
      setError(error.message);
      setBusy(false);
    }
  }

  return (
    <div>
      <button
        type="button"
        onClick={go}
        disabled={busy}
        className="w-full flex items-center justify-center gap-3 bg-surface border border-hairline-strong rounded-[--radius] px-4 py-2.5 text-sm font-medium text-ink hover:bg-surface-2 transition-colors disabled:opacity-60"
      >
        <svg viewBox="0 0 18 18" aria-hidden className="w-[18px] h-[18px] shrink-0">
          <path fill="#4285F4" d="M17.64 9.2c0-.64-.06-1.25-.16-1.84H9v3.48h4.84a4.14 4.14 0 0 1-1.8 2.72v2.26h2.92c1.7-1.57 2.68-3.88 2.68-6.62z" />
          <path fill="#34A853" d="M9 18c2.43 0 4.47-.8 5.96-2.18l-2.92-2.26c-.81.54-1.84.86-3.04.86-2.34 0-4.32-1.58-5.03-3.7H.96v2.33A9 9 0 0 0 9 18z" />
          <path fill="#FBBC05" d="M3.97 10.72a5.41 5.41 0 0 1 0-3.44V4.95H.96a9 9 0 0 0 0 8.1l3.01-2.33z" />
          <path fill="#EA4335" d="M9 3.58c1.32 0 2.5.45 3.44 1.35l2.58-2.58C13.46.89 11.43 0 9 0A9 9 0 0 0 .96 4.95l3.01 2.33C4.68 5.16 6.66 3.58 9 3.58z" />
        </svg>
        {busy ? "Redirecting…" : label}
      </button>
      {error && <p className="mt-2 text-[13px] text-danger">{error}</p>}
    </div>
  );
}
