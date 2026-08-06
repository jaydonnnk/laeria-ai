// Service worker: the only place that holds credentials or talks to the API.
//
// Content scripts run inside arbitrary shop pages and are treated as
// untrusted for this purpose — they detect and render, and ask this worker to
// do anything that needs a token. That is also why fetches live here: an MV3
// worker with host_permissions is not subject to page CORS, so no backend
// change was needed to support the extension.

import { getConfig } from "./config.js";

// ---- auth ---------------------------------------------------------------
//
// Supabase's REST auth endpoints directly, rather than bundling the JS SDK —
// two endpoints are all this needs, and the SDK's storage assumptions do not
// fit an extension.

async function supabaseFetch(path, body) {
  const { supabaseUrl, supabaseAnonKey } = await getConfig();
  if (!supabaseUrl || !supabaseAnonKey) {
    throw new Error("Supabase is not configured — open the extension settings");
  }
  const res = await fetch(`${supabaseUrl}/auth/v1/${path}`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      apikey: supabaseAnonKey,
      Authorization: `Bearer ${supabaseAnonKey}`,
    },
    body: JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(data.error_description || data.msg || data.error || `auth ${res.status}`);
  }
  return data;
}

async function storeSession(raw) {
  const session = {
    access_token: raw.access_token,
    refresh_token: raw.refresh_token,
    email: raw.user?.email ?? "",
    // Absolute ms, so a sleeping worker cannot be fooled by elapsed time.
    expires_at: Date.now() + (raw.expires_in ?? 3600) * 1000,
  };
  await chrome.storage.local.set({ session });
  return session;
}

export async function signIn(email, password) {
  return storeSession(await supabaseFetch("token?grant_type=password", { email, password }));
}

async function signOut() {
  await chrome.storage.local.remove("session");
}

/** A valid access token, refreshing if it is close to expiry. Null if signed out. */
async function getToken() {
  const { session } = await chrome.storage.local.get("session");
  if (!session) return null;
  // 60s of slack: a token that expires mid-flight reads as a session error.
  if (Date.now() < session.expires_at - 60_000) return session.access_token;
  if (!session.refresh_token) {
    await signOut();
    return null;
  }
  try {
    const next = await storeSession(
      await supabaseFetch("token?grant_type=refresh_token", {
        refresh_token: session.refresh_token,
      })
    );
    return next.access_token;
  } catch {
    await signOut();
    return null;
  }
}

// ---- backend ------------------------------------------------------------

async function api(path, options = {}) {
  const token = await getToken();
  if (!token) throw new Error("NOT_SIGNED_IN");
  const { apiUrl } = await getConfig();
  const res = await fetch(`${apiUrl}${path}`, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${token}`,
      ...(options.headers || {}),
    },
  });
  if (res.status === 401 || res.status === 403) {
    await signOut();
    throw new Error("NOT_SIGNED_IN");
  }
  if (!res.ok) throw new Error(`${res.status}: ${(await res.text()).slice(0, 200)}`);
  return res.json();
}

// The first poll is short on purpose: a query the backend already has cached
// finishes almost immediately, and waiting a full interval before asking makes
// an instant answer feel like a slow one.
const FIRST_POLL_MS = 700;
const POLL_MS = 1500;
const POLL_TIMEOUT_MS = 8 * 60 * 1000;

/** Submit research and poll to completion, reporting progress to the caller tab. */
async function runResearch(query, onProgress) {
  const { job_id } = await api("/research/decision/submit", {
    method: "POST",
    body: JSON.stringify({ query, thread_budget: 8 }),
  });
  const deadline = Date.now() + POLL_TIMEOUT_MS;
  let wait = FIRST_POLL_MS;
  for (;;) {
    await new Promise((r) => setTimeout(r, wait));
    wait = POLL_MS;
    const job = await api(`/research/jobs/${job_id}`);
    onProgress?.(job.elapsed_seconds);
    if (job.status === "done") {
      if (!job.result) throw new Error("job finished with no result");
      return job.result;
    }
    if (job.status === "error") throw new Error(job.error || "research failed");
    if (Date.now() > deadline) {
      throw new Error("research is taking unusually long — it may still finish");
    }
  }
}

// A verdict for the same product on the same page should not re-run on every
// reload. The backend caches research for 24h anyway; this avoids even the
// round trip, and keeps the badge instant on a page the user revisits.
const VERDICT_TTL_MS = 12 * 60 * 60 * 1000;

async function cachedVerdict(key) {
  const { verdicts = {} } = await chrome.storage.local.get("verdicts");
  const hit = verdicts[key];
  if (hit && Date.now() - hit.at < VERDICT_TTL_MS) return hit.brief;
  return null;
}

async function cacheVerdict(key, brief) {
  const { verdicts = {} } = await chrome.storage.local.get("verdicts");
  verdicts[key] = { at: Date.now(), brief };
  // Bounded: keep the 50 most recent.
  const entries = Object.entries(verdicts).sort((a, b) => b[1].at - a[1].at);
  await chrome.storage.local.set({ verdicts: Object.fromEntries(entries.slice(0, 50)) });
}

// ---- message router -----------------------------------------------------

const HANDLERS = {
  async AUTH_STATUS() {
    const { session } = await chrome.storage.local.get("session");
    const cfg = await getConfig();
    return {
      signedIn: Boolean(session),
      email: session?.email ?? "",
      configured: Boolean(cfg.supabaseUrl && cfg.supabaseAnonKey && cfg.apiUrl),
    };
  },

  async SIGN_IN({ email, password }) {
    const s = await signIn(email, password);
    return { signedIn: true, email: s.email };
  },

  async SIGN_OUT() {
    await signOut();
    return { signedIn: false };
  },

  async RESEARCH({ product, force }, sender) {
    // The cleaned query, not the page title. Content script derives it; the
    // backend's own cache is keyed on the exact string, so keying locally on
    // anything else would desync the two caches.
    const query = (product.query || product.title || "").trim();
    const key = query.toLowerCase();
    if (!force) {
      const hit = await cachedVerdict(key);
      if (hit) return { brief: hit, cached: true };
    }
    const tabId = sender?.tab?.id;
    const brief = await runResearch(query, (seconds) => {
      if (tabId != null) {
        chrome.tabs.sendMessage(tabId, { type: "RESEARCH_PROGRESS", seconds }).catch(() => {});
      }
    });
    await cacheVerdict(key, brief);
    return { brief, cached: false };
  },

  /** Order import: plan subreddits per item, then create the monitored item. */
  async IMPORT_ORDER({ items }) {
    const created = [];
    const failed = [];
    for (const item of items) {
      try {
        const plan = await api(`/research/subreddits?q=${encodeURIComponent(item.name)}`);
        const subreddits = (plan.subreddits || []).slice(0, 4);
        if (!subreddits.length) throw new Error("no communities identified");
        const row = await api("/monitor/items", {
          method: "POST",
          body: JSON.stringify({
            name: item.name.slice(0, 120),
            category: item.category || "",
            subreddits,
            check_interval_hours: 24,
          }),
        });
        created.push({ name: item.name, id: row.id, subreddits });
      } catch (e) {
        failed.push({ name: item.name, error: String(e.message || e) });
      }
    }
    return { created, failed };
  },
};

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  const handler = HANDLERS[msg?.type];
  if (!handler) return false;
  handler(msg, sender).then(
    (data) => sendResponse({ ok: true, data }),
    (err) => sendResponse({ ok: false, error: String(err.message || err) })
  );
  return true; // keep the channel open for the async reply
});
