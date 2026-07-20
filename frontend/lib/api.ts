// Thin client for the FastAPI backend. Attaches the Supabase Auth session
// token; a 401/403 response bounces the user to /login.

import { supabase } from "./supabase";

const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const { data } = await supabase.auth.getSession();
  const token = data.session?.access_token;
  if (!token && typeof window !== "undefined") {
    window.location.href = "/login";
    throw new Error("not signed in");
  }
  const res = await fetch(`${API_URL}${path}`, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${token}`,
      ...(options?.headers ?? {}),
    },
  });
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

  // Mode 2 (Phase 1)
  startDecision: (query: string, context = "", thread_budget = 8) =>
    request<ResearchBrief>("/research/decision", {
      method: "POST",
      body: JSON.stringify({ query, context, thread_budget }),
    }),

  // Mode 2 trigger: act on a strong brief
  actOnBrief: (p: { query: string; consensus_pick: string; confidence: string }) =>
    request<{ action: PayAction; outcome: string }>("/research/act", {
      method: "POST",
      body: JSON.stringify(p),
    }),

  // Mode 1 (Phase 2)
  startRetrospective: (decision: string, context = "", thread_budget = 8) =>
    request<OutcomeSummary>("/research/retrospective", {
      method: "POST",
      body: JSON.stringify({ decision, context, thread_budget }),
    }),

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

  // Hackathon Phase 2: disposable virtual cards
  listCards: () => request<Card[]>("/cards/"),
  testIssueCard: (amount_limit_usd: number, merchant_hint = "") =>
    request<Card>("/cards/test-issue", {
      method: "POST",
      body: JSON.stringify({ amount_limit_usd, merchant_hint }),
    }),
  cardDetails: (id: string) => request<CardDetails>(`/cards/${id}/details`),
  cancelCard: (id: string) =>
    request<Card>(`/cards/${id}/cancel`, { method: "POST" }),
  cardTransactions: (id: string) =>
    request<CardTransaction[]>(`/cards/${id}/transactions`),
};

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
  max_per_transaction: number;
  max_per_month: number;
  require_confirmation_above: number;
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
