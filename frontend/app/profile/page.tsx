"use client";

import { useCallback, useEffect, useState } from "react";
import { api, ShippingInfo } from "../../lib/api";
import { Button } from "../../components/ui/Button";
import { Card } from "../../components/ui/Card";
import { Badge } from "../../components/ui/Badge";
import { Banner, SectionHeader } from "../../components/ui/Banner";
import { Input, Field } from "../../components/ui/Input";

const EMPTY: ShippingInfo = {
  name: "",
  email: "",
  address1: "",
  city: "",
  postal_code: "",
  country_code: "",
  zone_code: "",
};

export default function ProfilePage() {
  const [shipping, setShipping] = useState<ShippingInfo>(EMPTY);
  const [complete, setComplete] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const p = await api.getProfile();
      setShipping({ ...EMPTY, ...p.shipping });
      setComplete(p.complete);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  async function save(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    setInfo(null);
    try {
      const p = await api.putProfile(shipping);
      setShipping({ ...EMPTY, ...p.shipping });
      setComplete(p.complete);
      setInfo(
        p.complete
          ? "Profile saved. The agent will ship here."
          : "Saved — but some required fields are still blank, so the agent falls back to the demo address until they're filled."
      );
      setTimeout(() => setInfo(null), 5000);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  const set = (k: keyof ShippingInfo) => (e: React.ChangeEvent<HTMLInputElement>) =>
    setShipping({ ...shipping, [k]: e.target.value });

  return (
    <main className="max-w-[1100px] mx-auto px-6 py-10 md:py-14">
      <div className="mb-8">
        <div className="eyebrow mb-3">Your details</div>
        <h1 className="text-2xl md:text-[2rem] font-semibold tracking-[-0.02em]">Profile</h1>
        <p className="mt-3 text-ink-muted max-w-[46rem]">
          Where the agent ships physical goods when it checks out on your behalf.
          These fields fill the merchant&apos;s checkout so nobody has to type them
          at purchase time — the whole point of an agent that pays.
        </p>
      </div>

      {error && <Banner tone="error" className="mb-3">{error}</Banner>}
      {info && <Banner tone="success" className="mb-3">{info}</Banner>}

      <section className="max-w-[560px]">
        <SectionHeader
          title="Shipping address"
          aside={complete ? undefined : "incomplete"}
        />
        <Card className="p-6">
          <div className="flex items-center gap-2 mb-4 flex-wrap">
            {complete ? (
              <Badge tone="success" dot>
                ready — agent can ship
              </Badge>
            ) : (
              <Badge tone="warning" dot>
                using demo address until complete
              </Badge>
            )}
          </div>

          <form onSubmit={save} className="grid gap-4">
            <Field label="Full name">
              <Input value={shipping.name} onChange={set("name")} placeholder="Jane Tan" />
            </Field>
            <Field label="Contact email">
              <Input
                type="email"
                value={shipping.email}
                onChange={set("email")}
                placeholder="jane@example.com"
              />
            </Field>
            <Field label="Street address">
              <Input
                value={shipping.address1}
                onChange={set("address1")}
                placeholder="123 Orchard Road, #04-01"
              />
            </Field>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
              <Field label="City">
                <Input value={shipping.city} onChange={set("city")} placeholder="Singapore" />
              </Field>
              <Field label="Postal code">
                <Input
                  value={shipping.postal_code}
                  onChange={set("postal_code")}
                  placeholder="238858"
                />
              </Field>
            </div>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
              <Field label="Country (2-letter code)">
                <Input
                  value={shipping.country_code}
                  onChange={set("country_code")}
                  placeholder="SG"
                  maxLength={2}
                  className="uppercase"
                />
              </Field>
              <Field label="State / province code">
                <Input
                  value={shipping.zone_code}
                  onChange={set("zone_code")}
                  placeholder="optional — e.g. CA"
                  maxLength={8}
                  className="uppercase"
                />
              </Field>
            </div>
            <p className="text-[13px] text-ink-muted">
              The demo storefront only ships to some countries — if checkout can&apos;t
              offer a shipping method for your address, the agent refuses before
              paying. A state/province code is only needed where the country has one.
            </p>
            <Button type="submit" disabled={busy} className="w-fit">
              {busy ? "Saving…" : "Save profile"}
            </Button>
          </form>
        </Card>
      </section>
    </main>
  );
}
