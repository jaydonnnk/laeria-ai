import { createClient } from "@supabase/supabase-js";

// NEXT_PUBLIC_* are inlined at build time. If they're missing during a
// build-time prerender, createClient() with an empty URL throws and fails the
// whole build. Fall back to a syntactically-valid placeholder so the build
// (and static prerender of these client pages) succeeds; the real values are
// inlined whenever the env vars are set, which they must be for auth to work.
const PLACEHOLDER = "https://placeholder.supabase.co";

// Guard against a missing OR malformed URL (e.g. no protocol, or the anon key
// pasted into the URL field). createClient does `new URL(url + "/auth/v1")`,
// which throws and fails the build-time prerender on a bad value. Fall back to
// a valid placeholder and warn instead of crashing.
function safeUrl(raw: string | undefined): string {
  const v = (raw ?? "").trim();
  try {
    return new URL(v).origin ? v : PLACEHOLDER;
  } catch {
    if (typeof window !== "undefined") {
      console.warn("NEXT_PUBLIC_SUPABASE_URL is missing or invalid:", v);
    }
    return PLACEHOLDER;
  }
}

const url = safeUrl(process.env.NEXT_PUBLIC_SUPABASE_URL);
const anonKey = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY || "placeholder-anon-key";

export const supabase = createClient(url, anonKey);
