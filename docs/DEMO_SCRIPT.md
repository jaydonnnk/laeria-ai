# Demo Script — laeria.ai @ Payment-Lifecycle Hackathon

Target: **under 3 minutes of live pipeline**, plus narration. Rehearse twice
on the stage laptop before submitting. Companion doc: `HACKATHON_SWAP.md`
(sponsor swaps + fallbacks).

## Pre-stage checklist (run 30 min before, in order)

1. Pause OneDrive sync (SAC/venv trap).
2. `cd backend && .venv/Scripts/python -m scripts.demo_e2e` → must print
   **READY FOR STAGE**. (`--buy` for a full silent rehearsal.)
3. Start backend: `uvicorn api.main:app --port 8000`
   Start frontend: `cd frontend && npm run dev`
4. `.env`: `BROWSER_HEADED=true` (audience watches the agent's browser),
   restart backend after the change.
5. Log in at localhost:3000, open `/commerce`, keep `/actions` in a second tab.
6. Mandate on `/actions`: per-txn **$50**, monthly **$500**, ask-first **$30**,
   autonomous **ON**. (The ask-first threshold below the price is what makes
   the purchase land in approval — the human-in-the-loop beat.)

   **Do NOT set monthly to 0.** This instruction used to read "monthly 0 (no
   cap)", which was true before Phase A and is now a demo-killer: an unset or
   zero cap means ZERO allowance, not unlimited, so every purchase would be
   refused on stage. There is deliberately no value meaning "no limit".

   Whatever you set, ask-first must sit **below** the product's price and
   per-txn **above** it, or you get the wrong beat: too low a per-txn cap
   refuses the purchase outright instead of parking it for approval.
7. Shopify admin → Orders open in a background tab (proof beat).
8. Screen recording running (Win+G Game Bar or OBS) — this run doubles as the
   fallback video if a later attempt dies.

## The demo (beats)

**0:00 — Frame (talk over `/commerce`)**
"laeria is a personal agent that already researches purchases on Reddit and
pays for things under a signed spending mandate — that part is prior work.
This weekend we gave it the full card lifecycle: fund, discover, issue,
execute — with the mandate enforced at every layer."

**0:20 — 1·Fund**
Point at live balances (agent/treasury USDC on testnet). Click **Fund agent**
if treasury has gas; show the explorer link. Narrate: "At production this leg
is StraitsX — fiat in, XUSD out, KYC'd account. On stage it's on-chain USDC."

**0:45 — 2·Discover**
Search "snowboard". "The agent isn't trusting the catalogue JSON — watch."
Click **Verify with agent**: a real browser opens the product page, re-reads
the price a human would see, screenshots it for the audit trail. Show the
green price-match line.

**1:10 — Buy → mandate gate**
Click **Buy with agent** on a product over the ask-first threshold → lands in
pending approval. Flip to `/actions`: "Nothing above my threshold moves
without me. This is a co-signed mandate, not a prompt." Approve.

**1:30 — 3·Issue + 4·Execute (the money shot)**
Narrate over the headed browser doing checkout: "The moment I approved, the
agent issued a disposable virtual card — spending limit pinned to this
purchase, nothing else. Before it types a single card digit it re-reads the
order total from the checkout page and checks it against the mandate a third
time. Then it fills, pays, and the card is dead before the confirmation page
finishes loading. One card, one purchase, no standing credential to steal."

**2:10 — Proof**
- Shopify admin → the order just appeared (real order number).
- `/commerce` execution log → order ref, card ••••, PAN-shim declaration,
  **Show audit screenshots** (checkout loaded / pre-submit / confirmation).
- Cards section → card status **canceled**.

**2:40 — The differentiator (close)**
"Most agent-payment demos show the happy path. Ours refuses." Set per-txn cap
to $5 on `/actions`, click Buy again → refusal appears with the reason in the
execution log. "Three independent price verifications, a network-level card
limit, and a disposable credential. The bug class where an agent overspends
because a price changed mid-flight — we found that bug in our own code, fixed
it, and built the demo around the defense."

**Sponsor lines (drop where natural):**
- Avalanche: "chain leg is one env var — Fuji is registered in the signer
  already" (show HACKATHON_SWAP.md if asked).
- StraitsX: "the card adapter is an interface; StraitsX's issuance sandbox is
  the production implementation — booth questions ready."
- Kite: "our mandate is app-side today; Kite's Agent Passport is the same
  policy object chain-side — the audit trail has a slot for its identity."

## Fallbacks (trigger → action)

| Trigger | Action |
|---|---|
| Venue wifi dead | `CARD_ISSUER=mock` already offline; storefront needs net — play the recording from the pre-stage run |
| Checkout DOM changed / Shopify hiccup | Re-run once; second failure → recording |
| Facilitator/sandbox creds broken | Stay Base Sepolia / mock (HACKATHON_SWAP.md fallbacks) |
| Fund button errors (no gas) | Skip live fund; balances still show — narrate the leg |
| Anything else twice in a row | Recording. Do not debug on stage |

## Traps (memorize)

- `pip install` of bitarray / ckzg / regex re-breaks signing (SAC stubs in
  `infra/*_stub/`). No dependency changes at the venue without re-copying.
- Monitor worker auto-starts (port 47931) — harmless, ignore it.
- Storefront password rotation breaks discovery — it's in `.env`.
- Mandate `0` = **no cap**, not zero dollars.
