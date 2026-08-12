# Demo Script — laeria.ai @ Payment-Lifecycle Hackathon

Target: **under 3 minutes of live pipeline**, plus narration. Rehearse twice
on the stage laptop before submitting. Companion doc: `HACKATHON_SWAP.md`
(sponsor swaps + fallbacks).

## Pre-stage checklist (run 30 min before, in order)

1. Pause OneDrive sync (SAC/venv trap).
2. `cd backend && .venv/Scripts/python -m scripts.check_chain` → must print
   **CHAIN READY**. Catches the two failures that otherwise surface mid-demo
   as unrelated-looking errors: no gas in either wallet, and a
   `STABLECOIN_CONTRACT` that isn't the token you think it is.
3. `.venv/Scripts/python -m scripts.demo_e2e` → must print **READY FOR
   STAGE**. (`--buy` for a full silent rehearsal — this also leaves a real
   order in the store, so do it early enough to reset.)
4. Start backend: `uvicorn api.main:app --port 8000`
   Start frontend: `cd frontend && npm run dev`
5. `.env`: `BROWSER_HEADED=true` (audience watches the agent's browser),
   restart backend after the change.
6. Log in at localhost:3000, open `/commerce`, keep `/actions` in a second tab.
7. Mandate on `/actions`: per-txn **$50**, monthly **$500**, ask-first **$30**,
   autonomous **ON**. (The ask-first threshold below the price is what makes
   the purchase land in approval — the human-in-the-loop beat.)

   **Do NOT set monthly to 0.** This instruction used to read "monthly 0 (no
   cap)", which was true before Phase A and is now a demo-killer: an unset or
   zero cap means ZERO allowance, not unlimited, so every purchase would be
   refused on stage. There is deliberately no value meaning "no limit".

   Whatever you set, ask-first must sit **below** the product's price and
   per-txn **above** it, or you get the wrong beat: too low a per-txn cap
   refuses the purchase outright instead of parking it for approval.
8. Shopify admin → Orders open in a background tab (proof beat).
9. Screen recording running (Win+G Game Bar or OBS) — this run doubles as the
   fallback video if a later attempt dies.

## The demo (beats)

**0:00 — Frame (talk over `/commerce`)**
"laeria is a personal agent that already researches purchases on Reddit and
pays for things under a signed spending mandate — that part is prior work.
This weekend we gave it the full card lifecycle: fund, discover, issue,
execute — with the mandate enforced at every layer."

**0:20 — 1·Fund**
Point at live balances — agent/treasury **XSGD on Avalanche Fuji**. Click
**Fund agent**, show the snowtrace link resolving.

Narrate, and say the stand-in part out loud rather than letting it be found:
"XSGD is issued on Avalanche C-Chain mainnet. Our agent refuses to move
mainnet funds from an API endpoint, so the funding leg runs on Fuji against a
stand-in with the same symbol and the same six decimals. The difference
between them is one config line — here."

If challenged, or if you have the twenty seconds, run it:
```
STABLECOIN_CONTRACT=0xb2F85b7AB3c2b6f62DF06dE6aE7D09c010a5096E \
  X402_NETWORK=eip155:43114 python -m scripts.check_chain
```
That reads StraitsX's production contract live and prints `XSGD, 6 decimals`.
Demonstrating the swap beats asserting it.

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

**2:10 — Proof (one line does most of the work)**

The execution log row now reads
`order VMNZ… · card ••••4242 · settled 32.95 XSGD 0x8f2a…` — that single line
is the whole submission: an order number and an on-chain transaction hash
side by side.

- `/commerce` execution log → order ref, card ••••, **settled … XSGD** with a
  live snowtrace link, PAN-shim declaration, **Show audit screenshots**.
- Shopify admin → the order just appeared (real order number).
- Cards section → card status **canceled**.

**Say the shim out loud here, before anyone asks.** "The card is real, capped
to this purchase, and dead. The merchant's processor is not real — a Shopify
dev store only exposes the Bogus Gateway, so the storefront gets a magic test
PAN. That's this env var. Everything else on this screen happened."

That costs eight seconds and removes the only finding a judge can make by
reading the source.

**2:25 — Why the card can't overspend**
"The card's limit isn't a policy — it's the minimum of the verified price, what
I approved, the mandate caps, **and the agent's on-chain XSGD balance**. It
cannot be issued for money the wallet doesn't hold. Then the purchase settles
back out of that same balance on-chain. Funding and checkout are the same
ledger, not two demos next to each other."

**2:40 — The differentiator (close)**
"Most agent-payment demos show the happy path. Ours refuses." Set per-txn cap
to $5 on `/actions`, click Buy again → refusal appears with the reason in the
execution log. "Three independent price verifications, a network-level card
limit, and a disposable credential. The bug class where an agent overspends
because a price changed mid-flight — we found that bug in our own code, fixed
it, and built the demo around the defense."

**Sponsor lines (drop where natural):**
- Avalanche: funding and settlement both run on Fuji, live, with snowtrace
  links on screen. The chain is not a slide.
- StraitsX: "the card adapter is written against the `CardIssuer` interface
  with ten tests; going live on their issuance sandbox is credentials, not
  code." **Do not imply it has ever spoken to StraitsX** — it hasn't.
- Kite is **not a sponsor of this event** and must not be mentioned. An earlier
  version of this script listed it; that was an error carried from a wrong
  sponsor list.

## Fallbacks (trigger → action)

| Trigger | Action |
|---|---|
| Venue wifi dead | `CARD_ISSUER=mock` already offline; storefront and chain need net — play the recording from the pre-stage run |
| Checkout DOM changed / Shopify hiccup | Re-run once; second failure → recording |
| Fund button errors (no gas) | `python -m scripts.check_chain` names it in one line. Skip live fund; balances still show — narrate the leg |
| Settlement fails after the order lands | Nothing to do. The receipt reads "ordered, not settled on-chain" by design — the purchase is real either way. Say that; it demonstrates the failure handling |
| Fuji RPC flaky | The balance-backing check fails closed, so purchases refuse rather than proceed unbacked. Re-run; second failure → recording |
| Anything else twice in a row | Recording. Do not debug on stage |

## Traps (memorize)

- `pip install` of bitarray / ckzg / regex re-breaks signing (SAC stubs in
  `infra/*_stub/`). No dependency changes at the venue without re-copying.
- Monitor worker auto-starts (port 47931) — harmless, ignore it.
- Storefront password rotation breaks discovery — it's in `.env`.
- **Reddit is fully blocked. Exactly three queries work, byte-exact:**

  ```
  is the Steam Deck OLED worth it in 2026
  best noise cancelling headphones for open office
  best budget mechanical keyboard for programming under $100
  ```

  `old.reddit.com` now returns 403 to every logged-out request — the shutdown
  announced 2026-06-30 has landed in full. Anything not in the recorded corpus
  returns LOW CONFIDENCE / no consensus, which on screen looks identical to the
  product not working. The research **plan** is part of the corpus too and only
  three were captured, and the fixture key is a raw SHA-256 of the query
  string: no normalisation, so capitalisation and the `$100` both matter. Typing
  "steam deck oled worth it" instead of the line above gets nothing.

  Set `REDDIT_SOURCE=fixture` for the demo. It is faster (no 403 round trip and
  pacing sleep per request) and deterministic, and a miss now degrades to an
  empty subreddit rather than killing the run.
- Mandate `0` or unset = **ZERO allowance**, not "no cap". There is
  deliberately no value meaning unlimited. This line previously said the
  opposite, which is the same demo-killer already fixed once in the checklist
  above — an agent that refuses every purchase on stage.
- **Both wallets need gas.** Funding and settlement are self-signed ERC-20
  transfers; the facilitator's gas sponsorship covers x402 payments only and
  does not touch them.
- Withdrawing AVAX on **X-Chain instead of C-Chain** leaves the faucet gate
  unsatisfied and the balance stranded.
