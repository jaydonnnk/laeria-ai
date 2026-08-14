"use client";

import { useCallback, useEffect, useState } from "react";
import { Card } from "../ui/Card";
import { Button } from "../ui/Button";
import { Badge } from "../ui/Badge";
import { Input, Field } from "../ui/Input";
import { api, WalletAllowance, WalletOperator } from "../../lib/api";
import {
  approveSpending,
  connectWallet,
  ensureChain,
  hasWallet,
  toUnits,
} from "../../lib/wallet";

// rpc/explorer aren't in the operator response — the wallet only needs them to
// ADD a chain it doesn't have. Keyed by chain id so a new network is one entry.
const CHAINS: Record<number, { name: string; rpcUrl: string; explorer: string; nativeSymbol: string }> = {
  43113: {
    name: "Avalanche Fuji",
    rpcUrl: "https://api.avax-test.network/ext/bc/C/rpc",
    explorer: "https://testnet.snowtrace.io",
    nativeSymbol: "AVAX",
  },
  43114: {
    name: "Avalanche C-Chain",
    rpcUrl: "https://api.avax.network/ext/bc/C/rpc",
    explorer: "https://snowtrace.io",
    nativeSymbol: "AVAX",
  },
};

/** Non-custodial funding: connect your own wallet, approve the agent to spend
 *  up to a cap, and keep custody of everything above it. */
export function WalletConnect({ onChange }: { onChange?: () => void }) {
  const [state, setState] = useState<WalletAllowance | null>(null);
  const [operator, setOperator] = useState<WalletOperator | null>(null);
  const [amount, setAmount] = useState("30");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const noWallet = typeof window !== "undefined" && !hasWallet();

  const refresh = useCallback(async () => {
    try {
      const [a, o] = await Promise.all([api.walletAllowance(), api.walletOperator()]);
      setState(a);
      setOperator(o);
      setErr(null);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  async function connect() {
    setBusy(true);
    setErr(null);
    setMsg(null);
    try {
      const address = await connectWallet();
      await api.walletConnect(address);
      await refresh();
      onChange?.();
      setMsg(`Connected ${address.slice(0, 8)}…${address.slice(-4)}. Now approve a spending cap.`);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function approve() {
    if (!operator || !state?.address) return;
    setBusy(true);
    setErr(null);
    setMsg(null);
    try {
      const chain = CHAINS[operator.chain_id];
      await ensureChain(operator.chain_id, chain);
      const units = toUnits(amount, operator.token_decimals);
      const tx = await approveSpending({
        from: state.address,
        token: operator.token_contract,
        operator: operator.operator_address,
        amountUnits: units,
      });
      setMsg(
        `Approval sent (${tx.slice(0, 10)}…). Allowance updates once the block confirms.`
      );
      setTimeout(() => {
        refresh();
        onChange?.();
      }, 6000);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  const connected = !!state?.address && !state.custodial;
  const symbol = operator?.token_symbol ?? state?.token_symbol ?? "XSGD";

  return (
    <Card className="p-5 mb-4 border-accent/30">
      <div className="flex items-center gap-2 mb-3 flex-wrap">
        <Badge tone={connected ? "accent" : "neutral"} dot>
          {connected ? "self-custodied" : "not connected"}
        </Badge>
        <span className="text-sm font-medium text-ink">Connect your wallet</span>
      </div>
      <p className="text-sm text-ink-muted mb-4 max-w-[46rem]">
        Non-custodial: you hold the keys. You approve the agent to spend up to a
        cap; it can never touch a cent above it, and everything else stays in
        your wallet. Settlement moves your own {symbol} via that allowance.
      </p>

      {err && <p className="text-[13px] text-danger mb-3">{err}</p>}
      {msg && <p className="text-[13px] text-success mb-3">{msg}</p>}

      {noWallet ? (
        <p className="text-sm text-ink-subtle">
          No browser wallet detected — install MetaMask or Rabby to connect.
        </p>
      ) : !connected ? (
        <Button onClick={connect} disabled={busy}>
          {busy ? "Connecting…" : "Connect wallet"}
        </Button>
      ) : (
        <>
          <div className="grid sm:grid-cols-3 gap-4 mb-4 text-sm">
            <Stat label="wallet" value={`${state!.address.slice(0, 8)}…${state!.address.slice(-4)}`} mono />
            <Stat label={`${symbol} balance`} value={(state!.balance ?? 0).toFixed(2)} />
            <Stat label="agent allowance" value={(state!.allowance ?? 0).toFixed(2)} />
          </div>
          <div className="flex items-end gap-3">
            <Field label={`Approve cap (${symbol})`}>
              <Input
                type="number"
                value={amount}
                onChange={(e) => setAmount(e.target.value)}
                className="w-[160px]"
              />
            </Field>
            <Button onClick={approve} disabled={busy} variant="secondary">
              {busy ? "Approving…" : "Approve spending"}
            </Button>
            <button
              onClick={refresh}
              className="text-[12px] font-mono uppercase tracking-wide text-ink-subtle hover:text-ink pb-2"
            >
              refresh
            </button>
          </div>
          <p className="text-xs text-ink-subtle mt-3">
            The agent may spend up to the allowance shown above. Raise it by
            approving a higher cap; revoke by approving 0.
          </p>
        </>
      )}
    </Card>
  );
}

function Stat({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <div>
      <div className="eyebrow mb-1">{label}</div>
      <div className={`text-ink ${mono ? "font-mono text-[13px]" : "tnum text-lg"}`}>{value}</div>
    </div>
  );
}
