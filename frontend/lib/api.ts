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

export const api = {
  health: () => request<{ status: string }>("/health"),

  // Mode 2 (Phase 1)
  startDecision: (query: string, context = "") =>
    request("/research/decision", {
      method: "POST",
      body: JSON.stringify({ query, context }),
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
