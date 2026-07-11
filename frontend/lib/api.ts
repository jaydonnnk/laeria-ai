// Thin client for the FastAPI backend.
// Every feature call currently hits a 501 endpoint until its phase lands.

const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${API_URL}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!res.ok) {
    const detail = await res.text();
    throw new Error(`${res.status}: ${detail}`);
  }
  return res.json() as Promise<T>;
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
  failure_modes: string[];
  what_reviewers_miss: string[];
  alternatives: string[];
  red_flags: string[];
  confidence: "high" | "moderate" | "low";
  signal_quality: SignalQuality;
}

export const api = {
  health: () => request<{ status: string }>("/health"),

  // Mode 2 (Phase 1)
  startDecision: (query: string, context = "", thread_budget = 8) =>
    request<ResearchBrief>("/research/decision", {
      method: "POST",
      body: JSON.stringify({ query, context, thread_budget }),
    }),

  // Mode 1 (Phase 2)
  startRetrospective: (decision: string, context = "") =>
    request("/research/retrospective", {
      method: "POST",
      body: JSON.stringify({ decision, context }),
    }),

  // Mode 3 (Phase 3)
  listMonitoredItems: () => request("/monitor/items"),
  listAlerts: () => request("/monitor/alerts"),
};
