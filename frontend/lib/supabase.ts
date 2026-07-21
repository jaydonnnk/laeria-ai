import { createClient } from "@supabase/supabase-js";

// NEXT_PUBLIC_* are inlined at build time. If they're missing during a
// build-time prerender, createClient() with an empty URL throws and fails the
// whole build. Fall back to a syntactically-valid placeholder so the build
// (and static prerender of these client pages) succeeds; the real values are
// inlined whenever the env vars are set, which they must be for auth to work.
const url = process.env.NEXT_PUBLIC_SUPABASE_URL || "https://placeholder.supabase.co";
const anonKey = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY || "placeholder-anon-key";

export const supabase = createClient(url, anonKey);
