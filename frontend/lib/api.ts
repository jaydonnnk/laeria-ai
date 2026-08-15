// Thin client for the FastAPI backend. Attaches the Supabase Auth session
// token; a 401/403 response bounces the user to /login.

import { supabase } from "./supabase";

const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

// Requests that only read may be replayed safely; anything that spends money
// or mutates state must not be retried behind the user's back.
const RETRYABLE_METHODS = new Set(["GET", "HEAD"]);
const RETRY_DELAY_MS = 600;

/**
 * A backend error with its status kept, and its message unwrapped.
 *
 * FastAPI answers with `{"detail": "..."}`. Showing the raw body put
 * `400: {"detail":"..."}` in front of the user, which reads as a crash even
 * when the backend sent a deliberate, plainly-worded refusal. The status is
 * carried alongside so a page can tell a refusal (400) and a temporary outage
 * (503) apart from something actually breaking.
 */
export class ApiError extends Error {
  constructor(readonly status: number, message: string) {
    super(message);
    this.name = "ApiError";
  }

  /** The request was understood and declined. Retrying changes nothing. */
  get refused(): boolean {
    return this.status === 400;
  }

  /** A dependency was unavailable. Retrying later is the right move. */
  get unavailable(): boolean {
    return this.status === 503;
  }
}

async function apiError(res: Response): Promise<ApiError> {
  const body = await res.text();
  let message = body;
  try {
    const parsed = JSON.parse(body);
    if (typeof parsed?.detail === "string") message = parsed.detail;
  } catch {
    // Not JSON — the raw body is the best available message.
  }
  return new ApiError(res.status, message || `request failed (${res.status})`);
}

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const { data } = await supabase.auth.getSession();
  const token = data.session?.access_token;
  if (!token && typeof window !== "undefined") {
    window.location.href = "/login";
    throw new Error("not signed in");
  }

  const method = (options?.method ?? "GET").toUpperCase();
  const canRetry = RETRYABLE_METHODS.has(method);

  const send = () =>
    fetch(`${API_URL}${path}`, {
      ...options,
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${token}`,
        ...(options?.headers ?? {}),
      },
    });

  let res: Response;
  try {
    res = await send();
  } catch (err) {
    // A dropped connection surfaces as a TypeError with no status. These are
    // intermittent against the deployed backend and a single replay clears
    // almost all of them — without it, one blip banners a whole page.
    if (!canRetry) throw err;
    await new Promise((r) => setTimeout(r, RETRY_DELAY_MS));
    res = await send();
  }

  // 502/503/504 from a cold or restarting backend are worth one replay too.
  if (canRetry && [502, 503, 504].includes(res.status)) {
    await new Promise((r) => setTimeout(r, RETRY_DELAY_MS));
    res = await send();
  }

  if (res.status === 401 || res.status === 403) {
    if (typeof window !== "undefined") window.location.href = "/login";
    throw new Error("session expired — sign in again");
  }
  if (!res.ok) {
    throw await apiError(res);
  }
  return res.json() as Promise<T>;
}

interface JobState<T> {
  job_id: string;
  status: "pending" | "running" | "done" | "error";
  elapsed_seconds: number;
  result: T | null;
  error: string | null;
}

const POLL_INTERVAL_MS = 1500;
// Generous: Mode 1 legitimately runs 2-4 minutes. This only bounds a job that
// has stopped reporting, so it is a backstop rather than a deadline.
const POLL_TIMEOUT_MS = 8 * 60 * 1000;

/** Submit long research and poll until it finishes.
 *
 *  The work outlives any single HTTP request — Cloudflare cuts at 100s — so
 *  the server hands back a job id immediately and progress is polled.
 *  onProgress receives elapsed seconds so the UI can show real progress
 *  instead of an indefinite spinner. */
async function runJob<T>(
  submitPath: string,
  body: unknown,
  onProgress?: (seconds: number) => void
): Promise<T> {
  const { job_id } = await request<{ job_id: string }>(submitPath, {
    method: "POST",
    body: JSON.stringify(body),
  });

  const deadline = Date.now() + POLL_TIMEOUT_MS;
  for (;;) {
    await new Promise((r) => setTimeout(r, POLL_INTERVAL_MS));
    const job = await request<JobState<T>>(`/research/jobs/${job_id}`);
    onProgress?.(job.elapsed_seconds);

    if (job.status === "done") {
      if (job.result === null) throw new Error("job finished with no result");
      return job.result;
    }
    if (job.status === "error") {
      throw new Error(job.error ?? "research failed");
    }
    if (Date.now() > deadline) {
      throw new Error("research is taking unusually long — it may still finish; try again shortly");
    }
  }
}

// Mirrors backend core/models.py SourceThread.
export interface SourceThread {
  id: string;
  subreddit: string;
  title: string;
  url: string;
  score: number;
  num_comments: number;
}

// Mirrors backend core/models.py ConfidenceLevel.
export type ConfidenceLevel = "high" | "moderate" | "low";

// Mirrors backend core/models.py EvidenceState. WHY a brief looks the way it
// does, kept separate from how confident it is: "the community disagrees",
// "we found nothing" and "Reddit is blocked" are three different answers.
export type EvidenceState =
  | "ok"
  | "no_evidence"
  | "no_usable_evidence"
  | "source_unavailable"
  | "not_in_corpus"
  /** Threads were read and the safety layer refused every one of them. */
  | "unsafe_evidence";

// Mirrors backend core/models.py SignalQuality.
//
// Every field here is a backend observation. The UI renders these; it does not
// compute them. Deriving evidence quality client-side would let the page and
// the API disagree about the same brief, which is how "across r/a, r/b, r/c"
// came to sit above a source list containing only r/a.
export interface SignalQuality {
  /** Communities the planner set out to read — not proof any contributed. */
  subreddits_checked: string[];
  /** Communities actually present in the synthesised corpus. */
  subreddits_represented: string[];
  usable_thread_count: number;
  /** Deprecated alias for usable_thread_count; do not use in new code. */
  thread_count: number;
  /** Of the threads counted above, how many cleared score >= 10 AND comments
   *  >= 3. Measured over the same set, so it never exceeds them. */
  strong_thread_count: number;
  filters_relaxed: boolean;
  coordinated_posting_suspected: boolean;
  duplicate_threads_collapsed: number;
  similarity_analysis_available: boolean;
  /** Whether retrieved threads were screened for relevance to the question.
   *  False means the screen could not run, not that all were relevant. */
  relevance_screened: boolean;
  /** Search hits dropped as clearly off-topic before anything was read. */
  off_topic_candidates_rejected: number;
  /** Model-authored claims about the evidence structure that contradicted the
   *  authoritative evidence set and were therefore not displayed. */
  unverified_claims_removed: number;
  /** Threads the Bedrock guardrail refused. They are in none of the counts
   *  above — this is the only record that they existed. */
  unsafe_threads_excluded: number;
  /** Model-authored strings the guardrail refused on the way out. */
  guardrail_blocked_outputs: number;
  evidence_state: EvidenceState;
  date_range: string;
  bias_notes: string;
}

// Mirrors backend core/models.py ResearchBrief.
export interface ResearchBrief {
  consensus_pick: string;
  strengths: string[];
  failure_modes: string[];
  what_reviewers_miss: string[];
  alternatives: string[];
  red_flags: string[];
  /** The final verdict: min(semantic, structural ceiling). */
  confidence: ConfidenceLevel;
  /** What the model proposed from reading the threads. */
  semantic_confidence: ConfidenceLevel;
  /** The most the evidence shape could justify, whatever the model said. */
  structural_ceiling: ConfidenceLevel;
  /** Backend-authored reasons; one per policy rule that actually fired. */
  confidence_reasons: string[];
  signal_quality: SignalQuality;
  sources: SourceThread[];
}

// Mirrors backend core/models.py OutcomeSummary.
export interface OutcomeSummary {
  retrospective_count: number;
  threads_read: number;
  pct_positive: number;
  pct_negative: number;
  pct_mixed: number;
  common_positives: string[];
  common_regrets: string[];
  surprising_findings: string[];
  sample_bias: string;
  confidence: ConfidenceLevel;
  thin_coverage: boolean;
  sources: SourceThread[];
}

export const api = {
  health: () => request<{ status: string }>("/health"),

  // Who am I, and may I use the payment features? is_owner cannot be derived
  // client-side — OWNER_USER_ID only exists in the backend env.
  me: () => request<{ user_id: string; is_owner: boolean }>("/me"),

  // Mode 2 (Phase 1). Job-based: research runs longer than any proxy will
  // hold a connection open, so submit + poll rather than one long request.
  startDecision: (
    query: string,
    context = "",
    thread_budget = 8,
    onProgress?: (seconds: number) => void
  ) =>
    runJob<ResearchBrief>(
      "/research/decision/submit",
      { query, context, thread_budget },
      onProgress
    ),

  // Mode 2 trigger: act on a brief the SERVER already computed.
  //
  // Sends only what identifies the research run. The confidence and the pick
  // are read from the stored brief server-side and are not inputs — a client
  // cannot promote its own research or redirect the purchase by sending
  // different values. `context` is required for the lookup because /decision
  // researches "query (context)".
  actOnBrief: (p: { query: string; context?: string }) =>
    request<{ action: PayAction; outcome: string }>("/research/act", {
      method: "POST",
      body: JSON.stringify(p),
    }),

  // Mode 1 (Phase 2). Runs 2-4 minutes, so job-based is not optional here —
  // the synchronous route 504s behind any normal proxy.
  startRetrospective: (
    decision: string,
    context = "",
    thread_budget = 8,
    onProgress?: (seconds: number) => void
  ) =>
    runJob<OutcomeSummary>(
      "/research/retrospective/submit",
      { decision, context, thread_budget },
      onProgress
    ),

  // Mode 3 (Phase 3)
  listMonitoredItems: () => request<MonitoredItem[]>("/monitor/items"),
  createMonitoredItem: (item: {
    name: string;
    category?: string;
    subreddits: string[];
    check_interval_hours?: number;
  }) =>
    request<MonitoredItem>("/monitor/items", {
      method: "POST",
      body: JSON.stringify(item),
    }),
  deleteMonitoredItem: (id: string) =>
    request<{ deleted: string }>(`/monitor/items/${id}`, { method: "DELETE" }),
  checkItemNow: (id: string) =>
    request<{ run: MonitorRun | null; alert: MonitorAlert | null }>(
      `/monitor/items/${id}/check`,
      { method: "POST" }
    ),
  listAlerts: () => request<MonitorAlert[]>("/monitor/alerts"),
  dismissAlert: (id: string) =>
    request<{ dismissed: string }>(`/monitor/alerts/${id}/dismiss`, {
      method: "POST",
    }),
  syncObsidian: () =>
    request<{ suggestions: ObsidianSuggestion[] }>("/obsidian/sync", {
      method: "POST",
    }),

  // Phase 4: actions + mandate
  getMandate: () => request<Mandate>("/actions/mandate"),
  putMandate: (m: Mandate) =>
    request<Mandate>("/actions/mandate", { method: "PUT", body: JSON.stringify(m) }),
  listPayActions: () => request<PayAction[]>("/actions/"),
  proposeAction: (p: {
    type: string;
    target_url: string;
    category?: string;
    description?: string;
    rail?: "x402" | "card";
    product_handle?: string;
    variant_id?: string;
  }) =>
    request<{ action: PayAction; outcome: string }>("/actions/propose", {
      method: "POST",
      body: JSON.stringify(p),
    }),
  approveAction: (id: string) =>
    request<{ action: PayAction; outcome: string }>(`/actions/${id}/approve`, {
      method: "POST",
    }),
  rejectAction: (id: string) =>
    request<{ action: PayAction; outcome: string }>(`/actions/${id}/reject`, {
      method: "POST",
    }),
  bazaarSearch: (q = "", limit = 30) =>
    request<BazaarService[]>(
      `/actions/bazaar?q=${encodeURIComponent(q)}&limit=${limit}`
    ),
  usage: () => request<UsageStats>("/usage"),

  // Hackathon Phase 1: demo storefront discovery
  storeSearch: (q = "", limit = 12) =>
    request<StoreProduct[]>(
      `/store/search?q=${encodeURIComponent(q)}&limit=${limit}`
    ),
  storeVerify: (handle: string) =>
    request<StoreVerification>(`/store/product/${encodeURIComponent(handle)}/verify`, {
      method: "POST",
    }),
  // Discovery: free-text instruction in, one located product out. Returns a
  // proposal only — buying still goes through proposeAction and the mandate.
  storeShop: (instruction: string) =>
    request<ShopPick>("/store/shop", {
      method: "POST",
      body: JSON.stringify({ instruction }),
    }),

  // Hackathon Phase 2: disposable virtual cards
  listCards: () => request<Card[]>("/cards/"),
  // No test-issue binding on purpose: /cards/test-issue mints a card outside
  // the mandate pipeline and is disabled whenever APP_ENV != development, so a
  // UI affordance for it could only render a 403 on the deployed app.
  cardDetails: (id: string) => request<CardDetails>(`/cards/${id}/details`),
  cancelCard: (id: string) =>
    request<Card>(`/cards/${id}/cancel`, { method: "POST" }),
  cardTransactions: (id: string) =>
    request<CardTransaction[]>(`/cards/${id}/transactions`),

  // Hackathon Phase 4: funding
  walletBalances: () => request<WalletBalances>("/wallet/balances"),
  walletFund: (amount_usd: number) =>
    request<FundResult>("/wallet/fund", {
      method: "POST",
      body: JSON.stringify({ amount_usd }),
    }),

  // Audit-trail screenshots need the auth header, so <img src> can't load
  // them directly — fetch as a blob and hand back an object URL.
  screenshotUrl: async (category: "store" | "checkout", path: string) => {
    const filename = path.replace(/\\/g, "/").split("/").pop() ?? "";
    const { data } = await supabase.auth.getSession();
    const res = await fetch(
      `${API_URL}/store/screenshot/${category}/${encodeURIComponent(filename)}`,
      { headers: { Authorization: `Bearer ${data.session?.access_token}` } }
    );
    if (!res.ok) throw new Error(`screenshot ${res.status}`);
    return URL.createObjectURL(await res.blob());
  },
};

export interface WalletParty {
  address: string;
  token?: number;
  native?: number;
  error?: string;
}

export interface WalletBalances {
  network: string;
  network_id: string;
  native_symbol: string;
  // The funding token is whatever STABLECOIN_* configures — XSGD on Avalanche
  // for this build. Nothing here is named after a specific asset any more; the
  // duplicate `usdc`/`usdc_contract` keys that shadowed these are gone.
  token_contract?: string;
  token_symbol?: string;
  token_decimals?: number;
  agent: WalletParty;
  treasury: WalletParty;
}

export interface FundResult {
  tx_hash: string;
  explorer_url: string;
  amount_usd: number;
  token_symbol?: string;
  from: string;
  to: string;
  network: string;
}

// Mirrors backend cards table rows (no PAN/CVC — live-fetched only).
export interface Card {
  id: string;
  action_id: string | null;
  issuer: string;
  issuer_card_id: string;
  last4: string;
  exp_month: number;
  exp_year: number;
  spend_limit_usd: number;
  status: "issued" | "active" | "canceled";
  metadata: Record<string, unknown>;
  created_at: string;
  canceled_at: string | null;
}

// Mirrors backend services/cards.py CardDetails — shown transiently, never stored.
export interface CardDetails {
  number: string;
  cvc: string;
  exp_month: number;
  exp_year: number;
  brand: string;
  name: string;
}

export interface CardTransaction {
  id: string;
  amount_usd: number;
  type: string;
  created: string;
}

// Mirrors backend services/storefront.py _parse_product.
export interface StoreProduct {
  id: string;
  handle: string;
  title: string;
  price_usd: number;
  url: string;
  image: string;
  available: boolean;
  variant_id: string;
  product_type: string;
  vendor: string;
  tags: string;
}

// Mirrors backend agents/shopping_agent.py — the Discovery milestone's output.
// A proposal, never a purchase: `handle` and `variant_id` are what you hand to
// proposeAction, where the mandate decides whether it may execute.
export interface ShopRejection {
  handle: string;
  title: string;
  price_usd: number;
  why: string;
}

export interface ShopPick {
  found: boolean;
  instruction: string;
  query: string;
  max_price: number | null;
  handle: string;
  title: string;
  price_usd: number;
  variant_id: string;
  url: string;
  reason: string;
  rejected: ShopRejection[];
  candidates_seen: number;
  /** "browser" = the shop's own search page was read; "catalogue" = it wasn't. */
  scanned_via: string;
  scan_note: string;
  search_url: string;
  screenshot_path: string;
}

// Mirrors backend services/storefront.py verify_product.
export interface StoreVerification {
  handle: string;
  url: string;
  price_usd: number;
  available: boolean;
  screenshot_path: string;
  screenshot_data_url: string;
}

export interface UsageStats {
  reddit_requests: number;
  llm_calls: number;
  llm_tokens: number;
  embed_calls: number;
  paid_usd: number;
}

export interface BazaarService {
  resource: string;
  description: string;
  amount_usd: number;
  network: string;
  pay_to: string;
  payable_by_agent: boolean;
  last_updated: string;
}

export interface Mandate {
  // null = unset = zero allowance. No value expresses "unlimited".
  max_per_transaction: number | null;
  max_per_month: number | null;
  require_confirmation_above: number | null;
  allowed_categories: string[];
  blocked_vendors: string[];
  autonomous_actions_enabled: boolean;
}

export interface PayAction {
  id: string;
  type: string;
  target: string;
  status: "pending_approval" | "approved" | "executed" | "cancelled" | "failed";
  amount_usd: number;
  metadata: Record<string, unknown>;
  created_at: string;
}

// ---- Mode 3 types (mirror backend rows) ----

export interface RecentSignal {
  signal_level: "none" | "low" | "medium" | "high";
  ran_at: string;
  posts_found: number;
}

export interface MonitoredItem {
  id: string;
  name: string;
  category: string;
  subreddits: string[];
  check_interval_hours: number;
  active: boolean;
  last_checked_at: string | null;
  created_at: string;
  recent_signals?: RecentSignal[];
}

export interface MonitorRun {
  id: string;
  item_id: string;
  ran_at: string;
  posts_found: number;
  sentiment: string;
  signal_level: string;
}

export interface MonitorAlert {
  id: string;
  item_id: string;
  severity: "none" | "low" | "medium" | "high";
  summary: string;
  thread_urls: string[];
  recommended_action: string;
  actioned: boolean;
  created_at: string;
}

export interface ObsidianSuggestion {
  name: string;
  category: string;
  subreddits: string[];
}
