# Hackathon Swap Playbook (Aug 14–16, 2026)

Event-day integration checklist for the three sponsor swaps. Each section:
what flips, what code is already prepped, what to verify, and the fallback if
the sandbox fails on stage. Do NOT improvise beyond this doc at 11pm Friday.

Facts below verified 2026-07-20.

---

## Swap A — Avalanche Fuji (payment + funding chain)

**Verified:** the default facilitator (x402.org) does NOT support Fuji — its
`/supported` lists Base Sepolia (`eip155:84532`) + non-EVM chains only.
Avalanche runs its own x402 facilitator ecosystem instead:

| Facilitator | Notes |
|---|---|
| Thirdweb x402 | enterprise, EIP-7702 gasless; needs a thirdweb server wallet/key |
| PayAI | built for AI-agent payments on Avalanche |
| x402-rs | self-hosted Rust facilitator, zero fees |
| Ultravioleta DAO | gasless, multi-network |

Docs hub: https://build.avax.network/academy/blockchain/x402-payment-infrastructure/04-x402-on-avalanche/03-facilitators
Expect DevRel at the event to point at the blessed one — ask first.

**Config flip (.env):**
```
X402_NETWORK=eip155:43113
X402_FACILITATOR_URL=<facilitator from Avalanche DevRel>
AVALANCHE_FUJI_RPC_URL=https://api.avax-test.network/ext/bc/C/rpc   # already default
```

**Already prepped in code:**
- `X402_EXTRA_NETWORKS=eip155:43113` registers the signer for Fuji without
  changing the primary network (payment.py `_signer_client`) — verified builds.
- `services/wallet.py` has the full Fuji entry (chain 43113, USDC
  `0x5425890298aed601595a70AB815c96711a31Bc65`, snowtrace explorer) keyed off
  `X402_NETWORK` — balances + funding transfer work the moment the env flips.
- `bazaar.PAYABLE_NETWORKS` includes Fuji.

**Pre-event checklist (do BEFORE Friday):**
- [ ] Faucet AVAX to agent + treasury wallets: https://core.app/tools/testnet-faucet/ (needs a mainnet-funded wallet or coupon code — Avalanche hands out codes at events; ask if blocked)
- [ ] Faucet Fuji USDC (Circle faucet https://faucet.circle.com → Avalanche Fuji)
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

## Swap C — Kite (agent identity, stretch)

**Verified:** Kite Agent Passport = cryptographic agent identity + spend
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
