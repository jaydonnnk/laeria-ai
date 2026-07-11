"use client";

import { useCallback, useEffect, useState } from "react";
import {
  api,
  MonitoredItem,
  MonitorAlert,
  ObsidianSuggestion,
} from "../../lib/api";

const SIGNAL_COLOR: Record<string, string> = {
  none: "#2da44e",
  low: "#bf8700",
  medium: "#d4640c",
  high: "#cf222e",
};

export default function Page() {
  const [items, setItems] = useState<MonitoredItem[]>([]);
  const [alerts, setAlerts] = useState<MonitorAlert[]>([]);
  const [suggestions, setSuggestions] = useState<ObsidianSuggestion[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null); // item id being checked
  const [syncing, setSyncing] = useState(false);

  const refresh = useCallback(async () => {
    try {
      const [i, a] = await Promise.all([api.listMonitoredItems(), api.listAlerts()]);
      setItems(i);
      setAlerts(a);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  async function runCheck(id: string) {
    setBusy(id);
    setError(null);
    try {
      await api.checkItemNow(id);
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(null);
    }
  }

  async function remove(id: string) {
    if (!confirm("Stop monitoring this item and delete its history?")) return;
    await api.deleteMonitoredItem(id);
    await refresh();
  }

  async function dismiss(id: string) {
    await api.dismissAlert(id);
    await refresh();
  }

  async function syncVault() {
    setSyncing(true);
    setError(null);
    try {
      const res = await api.syncObsidian();
      setSuggestions(res.suggestions);
      if (res.suggestions.length === 0) {
        setError("Vault read OK, but no owned products/subscriptions were found in your notes.");
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSyncing(false);
    }
  }

  async function approveSuggestion(s: ObsidianSuggestion) {
    await api.createMonitoredItem({
      name: s.name,
      category: s.category,
      subreddits: s.subreddits,
    });
    setSuggestions((prev) => prev.filter((x) => x.name !== s.name));
    await refresh();
  }

  const openAlerts = alerts.filter((a) => !a.actioned);

  return (
    <main style={{ maxWidth: 860, margin: "60px auto", padding: "0 24px" }}>
      <a href="/">&larr; back</a>
      <h1>Monitoring</h1>
      <p style={{ color: "#555" }}>
        The agent watches Reddit for signal about things you own or subscribe
        to, and alerts on <em>change</em> — not routine complaint noise.
      </p>

      {error && <p style={{ color: "#cf222e" }}>{error}</p>}

      {openAlerts.length > 0 && (
        <section style={{ marginBottom: 28 }}>
          <h2>Alerts</h2>
          <div style={{ display: "grid", gap: 12 }}>
            {openAlerts.map((a) => (
              <div
                key={a.id}
                style={{
                  ...cardStyle,
                  borderLeft: `4px solid ${SIGNAL_COLOR[a.severity] ?? "#888"}`,
                }}
              >
                <div style={{ display: "flex", justifyContent: "space-between", gap: 12 }}>
                  <strong style={{ color: SIGNAL_COLOR[a.severity], textTransform: "uppercase", fontSize: 12 }}>
                    {a.severity}
                  </strong>
                  <button onClick={() => dismiss(a.id)} style={smallButtonStyle}>
                    dismiss
                  </button>
                </div>
                <p style={{ margin: "6px 0" }}>{a.summary}</p>
                {a.thread_urls.length > 0 && (
                  <p style={{ margin: 0, fontSize: 13 }}>
                    {a.thread_urls.map((u, i) => (
                      <a key={u} href={u} target="_blank" rel="noopener noreferrer" style={{ marginRight: 10, color: "#0969da" }}>
                        thread {i + 1}
                      </a>
                    ))}
                  </p>
                )}
              </div>
            ))}
          </div>
        </section>
      )}

      <section style={{ marginBottom: 28 }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
          <h2>Monitored items</h2>
          <button onClick={syncVault} disabled={syncing} style={smallButtonStyle}>
            {syncing ? "Reading vault…" : "Suggest from Obsidian vault"}
          </button>
        </div>

        {suggestions.length > 0 && (
          <div style={{ ...cardStyle, background: "#f0f7ff", marginBottom: 16 }}>
            <strong>From your vault — approve to start monitoring:</strong>
            <div style={{ display: "grid", gap: 8, marginTop: 10 }}>
              {suggestions.map((s) => (
                <div key={s.name} style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 12 }}>
                  <span>
                    {s.name}
                    <span style={{ color: "#888", fontSize: 13 }}>
                      {" "}· {s.subreddits.map((x) => `r/${x}`).join(", ")}
                    </span>
                  </span>
                  <span style={{ display: "flex", gap: 8 }}>
                    <button onClick={() => approveSuggestion(s)} style={smallButtonStyle}>
                      approve
                    </button>
                    <button
                      onClick={() => setSuggestions((prev) => prev.filter((x) => x.name !== s.name))}
                      style={{ ...smallButtonStyle, color: "#888" }}
                    >
                      skip
                    </button>
                  </span>
                </div>
              ))}
            </div>
          </div>
        )}

        <div style={{ display: "grid", gap: 12 }}>
          {items.length === 0 && (
            <p style={{ color: "#888" }}>Nothing monitored yet. Add an item below or sync from your vault.</p>
          )}
          {items.map((item) => (
            <div key={item.id} style={cardStyle}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: 12 }}>
                <div>
                  <strong>{item.name}</strong>
                  <span style={{ color: "#888", fontSize: 13 }}>
                    {" "}· {item.subreddits.map((s) => `r/${s}`).join(", ")} · every{" "}
                    {item.check_interval_hours}h
                  </span>
                </div>
                <div style={{ display: "flex", gap: 8 }}>
                  <button onClick={() => runCheck(item.id)} disabled={busy !== null} style={smallButtonStyle}>
                    {busy === item.id ? "checking…" : "check now"}
                  </button>
                  <button onClick={() => remove(item.id)} style={{ ...smallButtonStyle, color: "#cf222e" }}>
                    remove
                  </button>
                </div>
              </div>
              <div style={{ marginTop: 8, display: "flex", alignItems: "center", gap: 4 }}>
                {(item.recent_signals ?? [])
                  .slice()
                  .reverse()
                  .map((r, i) => (
                    <span
                      key={i}
                      title={`${r.signal_level} — ${r.posts_found} posts — ${new Date(r.ran_at).toLocaleString()}`}
                      style={{
                        width: 12,
                        height: 12,
                        borderRadius: "50%",
                        background: SIGNAL_COLOR[r.signal_level] ?? "#ddd",
                        display: "inline-block",
                      }}
                    />
                  ))}
                {(item.recent_signals ?? []).length === 0 && (
                  <span style={{ color: "#aaa", fontSize: 13 }}>no checks yet</span>
                )}
                <span style={{ color: "#888", fontSize: 12, marginLeft: 8 }}>
                  {item.last_checked_at
                    ? `last checked ${new Date(item.last_checked_at).toLocaleString()}`
                    : "never checked"}
                </span>
              </div>
            </div>
          ))}
        </div>
      </section>

      <AddItemForm onAdded={refresh} onError={setError} />
    </main>
  );
}

function AddItemForm({
  onAdded,
  onError,
}: {
  onAdded: () => Promise<void>;
  onError: (e: string) => void;
}) {
  const [name, setName] = useState("");
  const [subs, setSubs] = useState("");
  const [interval, setInterval_] = useState(6);
  const [saving, setSaving] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!name.trim() || !subs.trim() || saving) return;
    setSaving(true);
    try {
      await api.createMonitoredItem({
        name: name.trim(),
        subreddits: subs.split(",").map((s) => s.trim()).filter(Boolean),
        check_interval_hours: interval,
      });
      setName("");
      setSubs("");
      await onAdded();
    } catch (err) {
      onError(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  }

  return (
    <section>
      <h2>Add item</h2>
      <form onSubmit={submit} style={{ display: "grid", gap: 10, maxWidth: 520 }}>
        <input
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder='What you own/subscribe to — e.g. "Spotify Premium"'
          style={inputStyle}
          required
        />
        <input
          value={subs}
          onChange={(e) => setSubs(e.target.value)}
          placeholder='Subreddits to watch, comma-separated — e.g. "truespotify, spotify"'
          style={inputStyle}
          required
        />
        <label style={{ fontSize: 14, color: "#555" }}>
          Check every{" "}
          <select
            value={interval}
            onChange={(e) => setInterval_(Number(e.target.value))}
            style={{ ...inputStyle, width: "auto", padding: "4px 8px" }}
          >
            <option value={6}>6 hours</option>
            <option value={12}>12 hours</option>
            <option value={24}>24 hours</option>
            <option value={72}>3 days</option>
          </select>
        </label>
        <button type="submit" disabled={saving} style={buttonStyle}>
          {saving ? "Adding…" : "Start monitoring"}
        </button>
      </form>
    </section>
  );
}

const cardStyle: React.CSSProperties = {
  border: "1px solid #e1e4e8",
  borderRadius: 10,
  padding: "14px 18px",
};

const inputStyle: React.CSSProperties = {
  padding: "9px 12px",
  fontSize: 14,
  border: "1px solid #ccc",
  borderRadius: 8,
  fontFamily: "inherit",
};

const buttonStyle: React.CSSProperties = {
  padding: "10px 16px",
  fontSize: 15,
  fontWeight: 600,
  borderRadius: 8,
  border: "none",
  background: "#1f2328",
  color: "#fff",
  cursor: "pointer",
  width: "fit-content",
};

const smallButtonStyle: React.CSSProperties = {
  padding: "4px 10px",
  fontSize: 13,
  borderRadius: 6,
  border: "1px solid #ccc",
  background: "#fff",
  cursor: "pointer",
};
