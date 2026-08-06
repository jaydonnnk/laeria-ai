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

**Pre-event checklist:**
- [x] Faucet Fuji USDC — done 2026-08-06, treasury `0x3aDe…68d0` holds 20 USDC
      (Circle https://faucet.circle.com → Avalanche Fuji). Ungated.
- [ ] **BLOCKED — Faucet AVAX to treasury.** Every route gates on a nonzero
      mainnet C-Chain balance, and the gate is circular: core.app wants "a
      coupon OR mainnet AVAX > 0", and Avalanche Support requires mainnet AVAX
      before issuing the coupon. QuickNode and Chainlink gate the same way.
      Ask DevRel on the Friday, or have anyone with a Fuji balance send 2 AVAX
      directly — it is worthless testnet money and needs no faucet.
      Blocks ONLY `wallet.fund_agent`. x402 payments are gasless via the
      facilitator, so the demo's payment path is unaffected.
- [ ] Confirm balances show on /commerce with `X402_NETWORK=eip155:43113`

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

**Config flip (.env):**
```
CARD_ISSUER=straitsx
CHECKOUT_GATEWAY_PROFILE=real     # only if their sandbox card works on a real gateway
```

**Code to fill at event:** `StraitsXAdapter` in `backend/services/cards.py` —
5 methods against the `CardIssuer` ABC (issue / get_details / cancel /
list_transactions / healthcheck). Mock + Stripe adapters are the reference
implementations either side of it.

**Booth questions (ask Friday night):**
1. Sandbox base URL + auth scheme (API key? HMAC-signed requests? OAuth?)
2. Virtual card create endpoint: can a spend limit be set per card? Per-auth
   or total? (maps to `spending_limits` in our adapter)
3. Card credential retrieval: API-fetchable PAN/CVC in sandbox? (we never
   persist them — need live fetch)
4. Card termination endpoint (single-use disposal after checkout)
5. Transactions/authorizations list per card (receipt view)
6. Funding leg: sandbox XUSD/XSGD mint or test balance? Webhook on card auth?
7. Does their sandbox card work against Shopify's real card gateway, or do we
   keep the Bogus profile and simulate?

**Fallback:** `CARD_ISSUER=mock` + Bogus gateway — the full pipeline runs
offline; pitch the adapter architecture with the StraitsX mapping on a slide.

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
