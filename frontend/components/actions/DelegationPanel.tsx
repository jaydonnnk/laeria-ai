"use client";

import { useCallback, useEffect, useState } from "react";
import { Card } from "../ui/Card";
import { Button } from "../ui/Button";
import { Badge } from "../ui/Badge";
import { api, Delegation, Mandate } from "../../lib/api";
import { signMandateDelegation, toUnits } from "../../lib/wallet";

/** The "Delegate" step: the user signs their mandate with their own wallet, so
 *  the agent's authority is a signature they made — a credential, not a row. */
export function DelegationPanel() {
  const [del, setDel] = useState<Delegation | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      setDel(await api.getDelegation());
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  async function sign() {
    setBusy(true);
    setErr(null);
    setMsg(null);
    try {
      const [alw, op, mandate] = await Promise.all([
        api.walletAllowance(),
        api.walletOperator(),
        api.getMandate() as Promise<Mandate>,
      ]);
      if (!alw.address || alw.custodial) {
        throw new Error("Connect your wallet on the Commerce page first.");
      }
      const dec = op.token_decimals;
      const unit = (v: number | null | undefined) =>
        v == null ? "0" : toUnits(String(v), dec).toString();
      // Signs the CURRENTLY SAVED mandate. The backend rejects a signature
      // whose caps don't match the stored mandate, so save any edits first.
      const message = {
        maxPerTransaction: unit(mandate.max_per_transaction),
        maxPerMonth: unit(mandate.max_per_month),
        requireConfirmationAbove: unit(mandate.require_confirmation_above),
        autonomous: !!mandate.autonomous_actions_enabled,
        expiry: Math.floor(Date.now() / 1000) + 30 * 24 * 3600,
        nonce: Date.now(),
      };
      const sig = await signMandateDelegation(alw.address, op.chain_id, message);
      await api.signDelegation({ message, chain_id: op.chain_id, signature: sig });
      setMsg("Delegation signed and verified against your mandate.");
      await refresh();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  const good = del?.present && del.verified && !del.expired;
  const stale = del?.present && (!del.verified || del.expired);

  return (
    <Card className="p-5 mt-4">
      <div className="flex items-center gap-2 mb-2 flex-wrap">
        <span className="eyebrow">delegation</span>
        {good && <Badge tone="success" dot>signed &amp; verified</Badge>}
        {stale && <Badge tone="warning" dot>needs re-signing</Badge>}
        {!del?.present && <Badge tone="neutral" dot>unsigned</Badge>}
      </div>
      <p className="text-sm text-ink-muted max-w-[42rem] mb-3">
        Sign your mandate with your own wallet to issue a delegation credential —
        the agent&apos;s authority becomes a signature you made beforehand, not a
        setting we could have written. Save any cap changes first, then sign.
      </p>

      {err && <p className="text-[13px] text-danger mb-2">{err}</p>}
      {msg && <p className="text-[13px] text-success mb-2">{msg}</p>}

      {good && (
        <p className="text-[13px] text-ink-subtle font-mono mb-3">
          signed by {del!.signed_by?.slice(0, 8)}…{del!.signed_by?.slice(-4)} · expires{" "}
          {del!.expiry ? new Date(del!.expiry * 1000).toLocaleDateString() : "?"}
        </p>
      )}
      {stale && (
        <p className="text-[13px] text-warning mb-3">
          {del!.expired ? "Expired" : del!.reason ?? "No longer matches the mandate"} — re-sign.
        </p>
      )}

      <Button onClick={sign} disabled={busy} variant="secondary">
        {busy ? "Waiting for signature…" : good ? "Re-sign delegation" : "Sign delegation"}
      </Button>
    </Card>
  );
}
