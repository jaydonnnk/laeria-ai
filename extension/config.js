// Where the extension points. All three values are PUBLIC — the same ones the
// deployed web app ships in its client bundle — so there is nothing secret
// here. Fill them once; the popup's settings panel can override them at
// runtime without editing this file.
//
// Get them from frontend/.env.local, or the Vercel project's environment:
//   NEXT_PUBLIC_API_URL, NEXT_PUBLIC_SUPABASE_URL, NEXT_PUBLIC_SUPABASE_ANON_KEY

export const DEFAULTS = {
  apiUrl: "https://laeria-ai-backend.onrender.com",
  supabaseUrl: "",   // https://<project>.supabase.co
  supabaseAnonKey: "",
};

/** Stored overrides win, so the popup can configure a fresh install. */
export async function getConfig() {
  const { config } = await chrome.storage.local.get("config");
  return { ...DEFAULTS, ...(config || {}) };
}

export async function setConfig(patch) {
  const current = await getConfig();
  const next = { ...current, ...patch };
  await chrome.storage.local.set({ config: next });
  return next;
}
