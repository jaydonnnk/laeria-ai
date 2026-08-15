// Thin client for the FastAPI backend. Attaches the Supabase Auth session
// token; a 401/403 response bounces the user to /login.

import { supabase } from "./supabase";

const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

// Requests that only read may be replayed safely; anything that spends money
// or mutates state must not be retried behind the user's back.
const RETRYABLE_METHODS = new Set(["GET", "HEAD"]);
const RETRY_DELAY_MS = 600;

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
    const detail = await res.text();
    throw new Error(`${res.status}: ${detail}`);
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

// Mirrors backend core/models.py ResearchBrief.
export interface SignalQuality {
  subreddits_checked: string[];
  thread_count: number;
  date_range: string;
  bias_notes: string;
}

export interface ResearchBrief {
  consensus_pick: string;
  strengths: string[];
  failure_modes: string[];
  what_reviewers_miss: string[];
  alternatives: string[];
  red_flags: string[];
  confidence: "high" | "moderate" | "low";
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
  confidence: "high" | "moderate" | "low";
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

  // Mode 2 trigger: act on a strong brief
  actOnBrief: (p: { query: string; consensus_pick: string; confidence: string }) =>
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

  // Non-custodial: the user connects their own wallet, approves the operator,
  // and the agent spends via transferFrom within that allowance.
  walletConnect: (address: string) =>
    request<{ address: string; custodial: boolean }>("/wallet/connect", {
      method: "POST",
      body: JSON.stringify({ address }),
    }),
  walletOperator: () => request<WalletOperator>("/wallet/operator"),
  walletAllowance: () => request<WalletAllowance>("/wallet/allowance"),

  // WS3: the signed mandate delegation credential.
  signDelegation: (body: { message: Record<string, unknown>; chain_id: number; signature: string }) =>
    request<{ verified: boolean; signed_by: string; expiry: number }>(
      "/actions/mandate/delegation",
      { method: "POST", body: JSON.stringify(body) }
    ),
  getDelegation: () => request<Delegation>("/actions/mandate/delegation"),

  // Profile: the shipping details the agent needs to check out physical goods
  // on the card rail. Falls back to the deployment's env address until saved.
  getProfile: () => request<ProfileResponse>("/profile"),
  putProfile: (s: ShippingInfo) =>
    request<ProfileResponse>("/profile", { method: "PUT", body: JSON.stringify(s) }),

  // StraitsX non-custodial card: challenge -> user signs in wallet -> issue.
  cardChallenge: (wallet_address: string, cardholder_name: string, amount_sgd: number) =>
    request<CardChallenge>("/cards/straitsx/challenge", {
      method: "POST",
      body: JSON.stringify({ wallet_address, cardholder_name, amount_sgd }),
    }),
  cardIssue: (challenge: CardChallenge, signature: string) =>
    request<CardIssueResult>("/cards/straitsx/issue", {
      method: "POST",
      body: JSON.stringify({ challenge, signature }),
    }),
  cardView: (card_opaque_id: string, settlement_tx: string, wallet_address: string) =>
    request<CardView>("/cards/straitsx/view", {
      method: "POST",
      body: JSON.stringify({ card_opaque_id, settlement_tx, wallet_address }),
    }),
  // Buy a real product with an issued card: reads the card, drives the merchant
  // checkout, ships to the Profile address. Slow (real browser checkout).
  cardCheckout: (body: {
    card_opaque_id: string;
    settlement_tx: string;
    wallet_address: string;
    product_handle: string;
    variant_id: string;
    card_amount_sgd: number;
  }) => request<CardCheckoutResult>("/cards/straitsx/checkout", {
    method: "POST",
    body: JSON.stringify(body),
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

// Everything the frontend needs to build the ERC-20 approve(operator, cap) tx.
export interface WalletOperator {
  operator_address: string;
  token_contract: string;
  token_symbol: string;
  token_decimals: number;
  chain_id: number;
  network: string;
  network_id: string;
}

export interface WalletAllowance {
  address: string;
  custodial: boolean;
  operator_address: string;
  token_symbol: string;
  balance: number;
  allowance: number;
}

export interface Delegation {
  present: boolean;
  signed_by?: string;
  expiry?: number;
  signed_at?: number;
  expired?: boolean;
  verified?: boolean;
  reason?: string;
}

// The shipping details the agent ships physical goods to (card rail).
export interface ShippingInfo {
  name: string;
  email: string;
  address1: string;
  city: string;
  postal_code: string;
  country_code: string; // ISO-3166 alpha-2, e.g. "SG"
  zone_code: string; // state/province where a country needs one
}

export interface ProfileResponse {
  shipping: Partial<ShippingInfo>;
  complete: boolean; // true when the agent has everything it needs to ship
}

// StraitsX card issuance (non-custodial). The challenge is opaque to the UI
// except typed_data, which the wallet signs; it round-trips back to /issue.
export interface CardChallenge {
  cardapi_url: string;
  typed_data: unknown; // EIP-712 payload for eth_signTypedData_v4
  authorization: { from: string; [k: string]: unknown };
  [k: string]: unknown;
}

export interface CardIssueResult {
  card_opaque_id: string;
  settlement_tx: string;
  iframe_url?: string;
  amount_sgd?: string;
  card_html?: string;
  message?: string;
}

export interface CardView {
  card_opaque_id: string;
  iframe_url?: string;
  card_html?: string;
}

export interface CardCheckoutResult {
  order_reference: string;
  total_usd: number;
  gateway_profile: string;
  pan_shim: boolean;
  screenshots: string[];
  product_handle: string;
  card_opaque_id: string;
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
