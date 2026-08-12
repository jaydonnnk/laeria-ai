# Hackathon Swap Playbook (Aug 14–16, 2026)

Event-day integration checklist. Each section: what flips, what code is already
prepped, what to verify, and the fallback if the sandbox fails on stage. Do NOT
improvise beyond this doc at 11pm Friday.

Swaps A and B are the sponsors that matter — StraitsX hosts, Avalanche is title
partner. Swap C (Kite) is descoped; it is not a partner of this event.

Facts below verified 2026-07-20, sponsor list re-verified 2026-08-06.

---

## Swap A — Avalanche Fuji (payment + funding chain)

**Verified:** the default facilitator (x402.org) does NOT support Fuji — its
`/supported` lists Base Sepolia (`eip155:84532`) + non-EVM chains only.
Avalanche runs its own x402 facilitator ecosystem instead.

**RESOLVED 2026-08-06 — use Ultravioleta DAO. No DevRel question needed.**
Probed `https://facilitator.ultravioletadao.xyz/supported` directly:

```
v2  exact  eip155:43113  tokens=[('usdc', '0x54258902…', 6)]   ← Fuji
v2  exact  eip155:43114  tokens=[('usdc', '0xB97EF9Ef…', 6)]   ← mainnet
```

That is the exact protocol version (2), scheme (`exact`) and CAIP-2 network id
the Python SDK in `payment.py` already speaks, and the USDC contract matches
the Fuji entry in `services/wallet.py` byte for byte. `/health` returns
`{"status":"healthy"}`. It is a plain HTTP facilitator with the standard
`/verify` + `/settle` REST surface, so it drops into `X402_FACILITATOR_URL`
with no SDK change, no API key and no account.

Rejected alternatives, for the record:
- **Thirdweb** — the officially-featured option, but it is a JS SDK object
  (`facilitator({client, serverWalletAddress})`), not a URL. Needs a thirdweb
  account, a secret key and an ERC-4337 server wallet. Not reachable from
  Python without reimplementation.
- **PayAI** — plain URL (`https://api.payai.network`) and has a Python SDK, so
  it is the fallback, but `/supported` returned empty when probed.
- **x402-rs** — self-hosting removes a dependency but adds a service to run on
  the night. Unnecessary now.

Ultravioleta also covers 100% of gas, and the Avalanche academy confirms the
consequence: *"You don't need test AVAX because the facilitator will pay the
gas fee."* **So the blocked AVAX faucet does not affect x402 payments at all.**
It blocks only `wallet.fund_agent`, which is a plain ERC-20 transfer this
backend signs itself — that still needs gas in the treasury.

Docs hub: https://build.avax.network/academy/blockchain/x402-payment-infrastructure/04-x402-on-avalanche/03-facilitators

**Config flip (.env):**
```
X402_NETWORK=eip155:43113
X402_FACILITATOR_URL=https://facilitator.ultravioletadao.xyz
AVALANCHE_FUJI_RPC_URL=https://api.avax-test.network/ext/bc/C/rpc   # already default
```

**Already prepped in code:**
- `X402_EXTRA_NETWORKS=eip155:43113` registers the signer for Fuji without
  changing the primary network (payment.py `_signer_client`) — verified builds.
- `services/wallet.py` has the full Fuji entry (chain 43113, USDC
  `0x5425890298aed601595a70AB815c96711a31Bc65`, snowtrace explorer) keyed off
  `X402_NETWORK` — balances + funding transfer work the moment the env flips.
- `bazaar.PAYABLE_NETWORKS` includes Fuji.

**Gas is required. The earlier note that it blocked only `fund_agent` is out
of date** — on-chain settlement (agent → merchant, added 2026-08-12) is a
second self-signed ERC-20 transfer, so BOTH wallets need native AVAX. The
facilitator's gas sponsorship covers x402 payments only; it does not touch
transfers this backend signs itself.

**Faucet gate — resolved by decision, not by finding an ungated faucet.**
Every route (core.app, QuickNode, Chainlink) gates on a nonzero mainnet
C-Chain balance, and core.app's coupon path is circular. Two ways out:
ask someone with a Fuji balance to send some directly, or acquire a small
amount of mainnet AVAX to satisfy the gate. **Plan of record is the latter**
— the community-ask route was declined as too uncertain to schedule around.
See `README`/the walkthrough for the step order; the one thing that must not
be got wrong is withdrawing on **Avalanche C-Chain**, not X-Chain, since the
faucet reads C-Chain only.

Needed: ~0.05 AVAX total. Fuji gas is ~25 nAVAX against ~50k-gas transfers,
so a single 2-AVAX faucet drop is orders of magnitude more than enough.

**Pre-event checklist:**
- [x] Faucet Fuji USDC — done 2026-08-06, treasury `0x3aDe…68d0` holds 20 USDC
      (Circle https://faucet.circle.com → Avalanche Fuji). Ungated.
- [x] Facilitator re-probed 2026-08-12 — `/health` healthy, `/supported` still
      serves `x402Version 2 / exact / eip155:43113` with USDC
      `0x5425890298…`. It now also publishes v1 aliases (`avalanche-fuji`)
      across 114 entries; the v2 CAIP-2 entry the SDK speaks is unchanged.
- [x] Mainnet AVAX → treasury (**C-Chain**), then core.app faucet — done
      2026-08-13, 2 AVAX received on Fuji. The faucet gate was confirmed live
      before spending: *"Must either enter a valid coupon code or have an AVAX
      balance greater than zero on mainnet C-Chain"* — no threshold, any dust
      satisfies it.
- [x] 1 Fuji AVAX treasury → agent `0x7ecbe755…2ee5` (settlement gas)
- [x] **XSGD stand-in deployed 2026-08-13:**
      `0x4C41454F440a5869cb881895C26dB9d15Ab65cfA`
      (symbol `XSGD`, 6 decimals, 1,000,000 minted to treasury)
      https://testnet.snowtrace.io/address/0x4C41454F440a5869cb881895C26dB9d15Ab65cfA
- [x] `python -m scripts.check_chain` → **CHAIN READY**, all six checks

**Live `.env` for the Avalanche demo:**
```
X402_NETWORK=eip155:43113
X402_FACILITATOR_URL=https://facilitator.ultravioletadao.xyz
STABLECOIN_CONTRACT=0x4C41454F440a5869cb881895C26dB9d15Ab65cfA
STABLECOIN_SYMBOL=XSGD
STABLECOIN_DECIMALS=          # blank — read from decimals() on the contract
```

**x402 on Fuji is USDC-only.** The facilitator lists no other token for
`eip155:43113`. Once `STABLECOIN_CONTRACT` points at the XSGD stand-in, the
funding/settlement legs run in XSGD while the x402 rail still wants USDC —
and the agent holds 0 Fuji USDC (the 20 is in treasury). The four judged
milestones run on the card rail and never touch the facilitator, so this is
harmless for the submission. If the x402 rail is demoed at all, move some
Fuji USDC to the agent first.

---

## Swap A2 — XSGD itself (the funding asset)

**Verified on-chain 2026-08-12:** real XSGD is at
`0xb2F85b7AB3c2b6f62DF06dE6aE7D09c010a5096E` on Avalanche C-Chain **mainnet**,
symbol `XSGD`, **6 decimals**. Read directly via `probe_token()` — this is no
longer "reportedly". There is still no public Fuji deployment.

That leaves a hard constraint: `WalletService._transfer` refuses to move funds
on any mainnet from an API endpoint, so the only chain where XSGD exists is the
one chain where the Funding and Settlement legs cannot run. Mainnet gives a
read-only balance and nothing else — and the balance would read 0.00 without an
XSGD position to show.

**Plan of record: deploy `infra/contracts/XSGDTest.sol` on Fuji.** Same symbol,
same 6 decimals, whole supply minted to the treasury on deploy. It is a testnet
stand-in and the contract name says so, so anyone reading it on snowtrace can
see that without being told.

What this does and does not claim:
- It does NOT claim integration with StraitsX's issued asset. We minted a token
  and named it XSGD. Say that first, before a judge says it.
- It DOES show the pipeline moving an arbitrary configured 6-decimal token on
  Avalanche, which is exactly what the real swap is —`STABLECOIN_CONTRACT` is
  the only difference.

**Demonstrate the claim rather than asserting it.** These two commands are the
strongest 20 seconds available on this milestone:

```
STABLECOIN_CONTRACT=0xb2F85b7AB3c2b6f62DF06dE6aE7D09c010a5096E \
  X402_NETWORK=eip155:43114 python -m scripts.check_chain   # reads real XSGD
python -m scripts.check_chain                               # the Fuji stand-in
```

The first reads StraitsX's production contract live, with no gas and no key,
and prints `XSGD, 6 decimals`. The second is the same code on the chain where
funds may actually move.

**Verify at event:** `python -m tests.test_environment` (wallet RPC check),
then one $0.01 vendor purchase if a facilitator is wired.

**Fallback:** stay on Base Sepolia (`eip155:84532` + x402.org facilitator) —
everything already works there; pitch the one-env-var swap instead of the
live swap.

---

## Swap B — StraitsX (card issuance + funding narrative)

**Verified:** StraitsX HAS a public Card Issuing API: https://docs.straitsx.com/v1-CARDS/docs/introduction
(sandbox environment exists; signing flow testable in sandbox before
production). They issue XSGD/XUSD and are MAS-licensed — the "stablecoin →
KYC'd account → card" story is literally their product.

### PLANNING ASSUMPTION (decided 2026-08-12)

**Assume StraitsX provides build ideas and nothing else** — no sandbox
credentials, no testnet XSGD, no card that clears a live gateway. Everything
below is written to that assumption. If they hand over more on the Friday it is
a one-env-var improvement, never a rescue.

Three reasons this is the right posture rather than pessimism:

1. Their public product is a **Cards** API sandbox. Nothing in it is a token
   faucet; testnet XSGD would be a separate thing built for separate reasons.
2. XSGD is MAS-regulated e-money whose whole proposition is a 1:1 SGD reserve.
   A testnet version has no reserve. Regulated issuers ship those less readily
   than a developer-first issuer like Circle does for USDC.
3. Even in the good case it lands Friday night. Anything on the critical path
   that resolves Friday night is a hope, not a plan.

**Consequences, all already true in the repo:**

| | Plan of record |
|---|---|
| Card issuer | `CARD_ISSUER=mock` — Stripe Issuing is unavailable to SG accounts |
| Gateway | `CHECKOUT_GATEWAY_PROFILE=bogus` — the PAN shim stays |
| Funding asset | Fuji XSGD stand-in (Swap A2) |
| The XSGD story | rests on OUR wiring — the balance-backed card ceiling and on-chain settlement in `a3124f6` — not on their product |

**Because the shim stays, the honesty beat is compulsory, not optional.** The
issued card never touches the transaction under `bogus`: `checkout.py` enters
the magic PAN `"1"`, and under `mock` there is not even a simulated
authorization. Say it before a judge finds it, and show the env var that flips
it. Being first about this reads as rigour; being caught reads as the opposite.

**`StraitsXAdapter` is an architecture exhibit, not an integration.** It is a
working config-driven HTTP client with 10 tests proving it survives whichever
field spellings a sandbox uses — every unknown is an env var. Pitch it as "the
adapter is written; going live is credentials", which is true and checkable.
Do not imply it has ever spoken to StraitsX.

**Config flip, if they surprise us:**
```
CARD_ISSUER=straitsx
CHECKOUT_GATEWAY_PROFILE=real     # only if their sandbox card clears a real gateway
```

**Booth questions — trimmed.** The old list of seven assumed an integration
sprint that is no longer planned. Ask only what could still change the demo,
and do not spend event time on the rest:

1. Is there a testnet XSGD deployment, or can sandbox XSGD be minted?
   (The only answer that would replace the Swap A2 stand-in.)
2. Would a sandbox card clear a real card gateway? (The only answer that
   would remove the PAN shim.)

Everything else — auth scheme, limit granularity, PAN retrieval, terminate vs
freeze — is already absorbed by env vars and the tolerant field reader. There
is nothing to learn at the booth that the adapter does not already handle.

**Fallback:** none needed. The assumption above IS the fallback, and the full
pipeline runs on it offline.

---

## Swap C — Kite (DESCOPED — not a sponsor)

**2026-08-06:** the Luma listing names StraitsX as host and Avalanche, AWS,
Convergence Summit and SMU Fintech / SMU AI as partners. Kite is not involved.
Everything below stands as research; none of it should consume event time.
Two corrections to it, from the Kite docs:

- The "registers extra networks via `X402_EXTRA_NETWORKS` if they speak x402"
  line is not established. Kite's service-provider guide documents scheme
  `gokite-aa` on `kite-testnet` (chain 2368, facilitator
  `facilitator.pieverse.io`); `payment.py` registers the standard `exact`
  scheme, and Kite is absent from Coinbase's x402 network-support list.
- A hosted service cannot hold passports for its users. Kite's only
  server-shaped mode is "developer mode" — the developer's own Passport pays
  and the developer bills customers separately. Personal use needs a
  device-bound passkey and a local command-running agent.

The `agent_identity` slide framing below is still the right answer, and costs
nothing.

**Original research:** Kite Agent Passport = cryptographic agent identity + spend
rules + verifiable receipts; mainnet since Apr 2026, testnet live with test
USDT. Docs: https://docs.gokite.ai/ (developer guide:
/kite-agent-passport/developer-guide). Three integration modes incl. MCP.

**Minimal integration (cheap, do if time):** attach a Kite Passport identity
to the audit trail — an `agent_identity` field in `actions.metadata` at
propose time. No payment-path change. Their spend-rules concept mirrors our
mandate — one slide: "our mandate = Kite passport policy, enforced app-side
today, chain-side via Kite tomorrow."

**Stretch (only if Sat morning is calm):** route one payment over Kite
testnet (test USDT). Their chain is EVM — the signer registers extra networks
via `X402_EXTRA_NETWORKS` if they speak x402; otherwise skip.

**Booth questions:**
1. Fastest path to a Passport for an existing Python agent (SDK? REST?)
2. Can a passport's spend rules be read/verified by a third party (judge demo)?
3. x402 compatibility on Kite chain — facilitator available?

**Fallback:** slide-only mention. Zero code risk.

---

## Demo-day environment gotchas (all machines)

- Windows Smart App Control blocks unsigned DLLs. Three packages are stubbed
  in this venv: `bitarray`, `ckzg`, `regex` (stubs live in `infra/*_stub/`).
  **Any `pip install` that reinstalls one of these re-breaks wallet/x402
  signing.** After ANY dependency change: re-copy stubs, run
  `python -m tests.test_environment`.
- `.venv` sits inside OneDrive — pause OneDrive sync during the event.
- Monitor worker auto-starts via Startup shortcut (port-lock 47931) — harmless,
  but don't be surprised by it in Task Manager.
- Storefront password cookie: `StorefrontService` handles unlock; if the dev
  store password is rotated, update `SHOP_STOREFRONT_PASSWORD`.
