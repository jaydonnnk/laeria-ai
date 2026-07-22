"use client";

import { useCallback, useEffect, useState } from "react";
import { api, MonitoredItem, MonitorAlert, ObsidianSuggestion } from "../../lib/api";
import { Button } from "../../components/ui/Button";
import { Card } from "../../components/ui/Card";
import { Badge } from "../../components/ui/Badge";
import { Banner, SectionHeader } from "../../components/ui/Banner";
import { Input, Select, Field } from "../../components/ui/Input";

const SIGNAL_BG: Record<string, string> = {
  none: "var(--color-success)",
  low: "var(--color-warning)",
  medium: "#d4640c",
  high: "var(--color-danger)",
};

export default function MonitorPage() {
  const [items, setItems] = useState<MonitoredItem[]>([]);
  const [alerts, setAlerts] = useState<MonitorAlert[]>([]);
  const [suggestions, setSuggestions] = useState<ObsidianSuggestion[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
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
    await api.createMonitoredItem({ name: s.name, category: s.category, subreddits: s.subreddits });
    setSuggestions((prev) => prev.filter((x) => x.name !== s.name));
    await refresh();
  }

  const openAlerts = alerts.filter((a) => !a.actioned);

  return (
    <main className="max-w-[1100px] mx-auto px-6 py-10 md:py-14">
      <div className="mb-8">
        <div className="eyebrow mb-3">Mode 3 · after you commit</div>
        <h1 className="text-2xl md:text-[2rem] font-semibold tracking-[-0.02em]">Monitoring</h1>
        <p className="mt-3 text-ink-muted max-w-[46rem]">
          The agent watches Reddit for signal about things you own or subscribe
          to, and alerts on <em>change</em> — not routine complaint noise.
        </p>
      </div>

      {error && <Banner tone="error" className="mb-3">{error}</Banner>}

      {openAlerts.length > 0 && (
        <section className="mb-10">
          <SectionHeader title="Alerts" aside={`${openAlerts.length} open`} />
          <div className="grid gap-3">
            {openAlerts.map((a) => (
              <Card
                key={a.id}
                className="p-5 border-l-2"
                style={{ borderLeftColor: SIGNAL_BG[a.severity] ?? "var(--color-hairline-strong)" }}
              >
                <div className="flex items-start justify-between gap-3">
                  <Badge status={a.severity}>{a.severity}</Badge>
                  <Button variant="ghost" size="sm" onClick={() => dismiss(a.id)}>
                    dismiss
                  </Button>
                </div>
                <p className="my-2 text-ink">{a.summary}</p>
                {a.recommended_action && a.recommended_action !== "none" && (
                  <p className="text-[13px] text-warning">
                    Agent recommends: <b>{a.recommended_action.replace("_", " ")}</b> —{" "}
                    <a href="/actions" className="text-info hover:underline">review in Actions</a>
                  </p>
                )}
                {a.thread_urls.length > 0 && (
                  <p className="mt-1 text-[13px] flex flex-wrap gap-3">
                    {a.thread_urls.map((u, i) => (
                      <a key={u} href={u} target="_blank" rel="noopener noreferrer" className="text-info hover:underline">
                        thread {i + 1} ↗
                      </a>
                    ))}
                  </p>
                )}
              </Card>
            ))}
          </div>
        </section>
      )}

      <section className="mb-10">
        <div className="flex items-center justify-between mb-4">
          <SectionHeader title="Monitored items" className="mb-0" />
          <Button variant="secondary" size="sm" onClick={syncVault} disabled={syncing}>
            {syncing ? "Reading vault…" : "Suggest from Obsidian vault"}
          </Button>
        </div>

        {suggestions.length > 0 && (
          <Card className="p-5 mb-4 bg-info-soft border-info/20">
            <div className="eyebrow mb-3">From your vault — approve to start monitoring</div>
            <div className="grid gap-2.5">
              {suggestions.map((s) => (
                <div key={s.name} className="flex items-center justify-between gap-3">
                  <span className="text-sm">
                    {s.name}
                    <span className="text-ink-subtle"> · {s.subreddits.map((x) => `r/${x}`).join(", ")}</span>
                  </span>
                  <span className="flex gap-2 shrink-0">
                    <Button size="sm" onClick={() => approveSuggestion(s)}>approve</Button>
                    <Button variant="ghost" size="sm" onClick={() => setSuggestions((p) => p.filter((x) => x.name !== s.name))}>
                      skip
                    </Button>
                  </span>
                </div>
              ))}
            </div>
          </Card>
        )}

        <div className="grid gap-3">
          {items.length === 0 && (
            <p className="text-ink-subtle text-sm">
              Nothing monitored yet. Add an item below or sync from your vault.
            </p>
          )}
          {items.map((item) => (
            <Card key={item.id} className="p-5">
              <div className="flex items-start justify-between gap-3">
                <div>
                  <span className="font-semibold text-ink">{item.name}</span>
                  <span className="text-[13px] text-ink-subtle">
                    {" "}· {item.subreddits.map((s) => `r/${s}`).join(", ")} · every{" "}
                    <span className="tnum">{item.check_interval_hours}</span>h
                  </span>
                </div>
                <div className="flex gap-2 shrink-0">
                  <Button variant="secondary" size="sm" onClick={() => runCheck(item.id)} disabled={busy !== null}>
                    {busy === item.id ? "checking…" : "check now"}
                  </Button>
                  <Button variant="danger" size="sm" onClick={() => remove(item.id)}>remove</Button>
                </div>
              </div>
              <div className="mt-3 flex items-center gap-1.5 flex-wrap">
                {(item.recent_signals ?? [])
                  .slice()
                  .reverse()
                  .map((r, i) => (
                    <span
                      key={i}
                      title={`${r.signal_level} — ${r.posts_found} posts — ${new Date(r.ran_at).toLocaleString()}`}
                      className="w-3 h-3 rounded-full inline-block"
                      style={{ background: SIGNAL_BG[r.signal_level] ?? "var(--color-hairline-strong)" }}
                    />
                  ))}
                {(item.recent_signals ?? []).length === 0 && (
                  <span className="text-ink-subtle text-[13px]">no checks yet</span>
                )}
                <span className="text-ink-subtle text-xs ml-2 tnum">
                  {item.last_checked_at
                    ? `last checked ${new Date(item.last_checked_at).toLocaleString()}`
                    : "never checked"}
                </span>
              </div>
            </Card>
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
      <SectionHeader title="Add item" />
      <Card className="p-6">
        <form onSubmit={submit} className="grid gap-4 max-w-[520px]">
          <Field label="What you own / subscribe to">
            <Input value={name} onChange={(e) => setName(e.target.value)} placeholder='e.g. "Spotify Premium"' required />
          </Field>
          <Field label="Subreddits to watch" hint="comma-separated">
            <Input value={subs} onChange={(e) => setSubs(e.target.value)} placeholder='e.g. "truespotify, spotify"' required />
          </Field>
          <Field label="Check interval">
            <Select value={interval} onChange={(e) => setInterval_(Number(e.target.value))} className="w-auto">
              <option value={6}>Every 6 hours</option>
              <option value={12}>Every 12 hours</option>
              <option value={24}>Every 24 hours</option>
              <option value={72}>Every 3 days</option>
            </Select>
          </Field>
          <Button type="submit" disabled={saving} className="w-fit">
            {saving ? "Adding…" : "Start monitoring"}
          </Button>
        </form>
      </Card>
    </section>
  );
}
