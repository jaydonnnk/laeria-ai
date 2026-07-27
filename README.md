# laeria.ai

An agent that decides *what* to buy from real community discussion, then buys
it under a spending mandate it can prove it obeyed.

Most purchase research is ranked by SEO budget. laeria reads the communities
that actually own the thing — the three-year update threads, the regret posts,
the "I'd buy it again" follow-ups — synthesises an honest consensus, and, when
that consensus is strong, completes the purchase itself on a disposable card
whose limit is pinned to that one buy.

Three modes:

1. **What to buy** (Mode 2) — multi-subreddit research before you commit,
   returning a structured consensus brief with red flags and alternatives.
2. **How it went** (Mode 1) — update/outcome posts from people who already made
   the same call.
3. **Monitor** (Mode 3) — watches what you own for a change in the signal, and
   can act within the mandate.

## Status

All five phases are built and running. The backend is deployed at
`https://laeria-ai-backend.onrender.com`; the frontend is not yet deployed.

- [x] Phase 0 — Schema, FastAPI, environment validation
- [x] Phase 1 — Mode 2 decision synthesis
- [x] Phase 2 — Mode 1 retrospective mining
- [x] Phase 3 — Mode 3 monitoring
- [x] Phase 4 — Payments (x402 rail + disposable-card rail)
- [x] Phase 5 — Frontend, polish

Known gaps and the plan against them: [`docs/REMEDIATION_PLAN.md`](docs/REMEDIATION_PLAN.md).

## What is real, and what is a demo composite

Stated up front, because the distinction matters and a reader will find it
anyway.

**Real:**

- Reddit research, signal filtering, and LLM synthesis — the whole Mode 1/2/3
  pipeline runs against genuine Reddit content.
- x402 payments — real EIP-712 signatures, real facilitator verification, real
  on-chain settlement on Base Sepolia.
- Mandate enforcement — spend caps, category and vendor scoping, confirmation
  thresholds, and the approved-amount clamp are all enforced in code and
  covered by `backend/tests/test_mandate.py`.
- Card issuance — Stripe Issuing test mode issues a genuine virtual card with a
  real spending limit, and cancels it after one attempt.
- Browser checkout — Playwright drives a real Shopify storefront end to end.

**Composited for the demo:**

- **The merchant charge is simulated.** Under the default `bogus` gateway
  profile the storefront receives Shopify's Bogus Gateway magic PAN rather than
  the issued card number, and a matching authorization is then created on the
  real card via `stripe.test_helpers`. The authorization on the card is real;
  no money has moved through a card network. Set
  `CHECKOUT_GATEWAY_PROFILE=real` for the live-card path.
- **The storefront is ours.** A Shopify dev store we control, with a limited
  catalogue. There is no supply side; a consensus pick that the store does not
  stock hits an empty state.
- **The x402 vendor is ours.** `ACTION_VENDOR_URL` points at our own $0.01
  demo resource by default, because a consumer-goods x402 merchant does not
  exist to point it at.
- **Reddit results may be replayed.** See below.

## The Reddit data source

This is the project's binding constraint and it is not solved.

The pipeline reads logged-out `old.reddit.com` HTML. On 2026-06-30 Reddit
announced that old.reddit will require a login, rolling out over the following
month, explicitly to stop logged-out automated traffic. There is no durable,
sanctioned, affordable access path in place today; a Data API application under
the Responsible Builder Policy is the actual answer and is pending.

Until then, `REDDIT_SOURCE` controls where the HTML comes from:

| Mode | Behaviour |
|------|-----------|
| `live` | Fetch only. |
| `record` | Fetch and persist every response as a fixture. |
| `fixture` | Replay only — a miss is an error, never a silent fetch. |
| `live_then_fixture` | Try live; fall back to the recorded corpus on failure. **Default.** |

A frozen corpus lives in `backend/fixtures/reddit/` so demos cannot fail on a
403. Replay runs the real parsers, filters, and agents — only the source of the
bytes changes.

```bash
python -m scripts.capture_corpus            # capture the demo queries
python -m scripts.capture_corpus --verify   # prove it replays with no network
```

## Stack

| Layer        | Choice                                                    |
|--------------|-----------------------------------------------------------|
| Frontend     | Next.js 14 (App Router), Tailwind v4, GSAP/Lenis — Vercel |
| Backend API  | FastAPI — Render (Docker, Playwright-capable)             |
| Agents       | Python — same service                                      |
| Database     | Supabase (Postgres + pgvector + Auth + RLS)               |
| LLM          | OpenRouter                                                 |
| Reddit       | `old.reddit.com` HTML + record/replay corpus              |
| Obsidian     | Local REST API plugin (localhost:27124)                   |
| Payments     | x402 (Coinbase SDK, Base Sepolia) + Stripe Issuing        |
| Checkout     | Playwright against a Shopify dev store                    |

Single-tenant: the backend runs on the Supabase service-role key and gates
every request on `OWNER_USER_ID`. Multi-user requires threading a `user_id`
through the repository layer — see the remediation plan.

## Repository layout

```
frontend/          Next.js app
backend/
  api/             FastAPI route handlers
  agents/          Research, signal analysis, alert engine
  services/        Reddit, LLM, payment, cards, checkout, storefront, wallet
  workers/         Monitor worker
  core/            Config, logging, models
  db/              Supabase client and repositories
  fixtures/        Frozen Reddit corpus (record/replay)
  scripts/         Corpus capture, end-to-end demo runner
  tests/           Environment check + mandate truth table
infra/
  systemd/         Unit files for workers
  supabase/        SQL schema
docs/              Build guide, architecture notes, remediation plan
```

## Getting started

```bash
cd backend
cp .env.example .env      # fill in credentials
python -m venv .venv && .venv/Scripts/activate   # or source .venv/bin/activate
pip install -r requirements.txt
python -m tests.test_environment                 # connectivity check
python -m pytest tests/test_mandate.py           # mandate truth table
```

```bash
cd frontend
npm install && npm run dev
```

### Safety defaults worth knowing

- **An unset spending cap means zero allowance, not unlimited.** There is no
  value in the mandate model that expresses "no limit".
- **Base mainnet signing is off** unless `X402_ALLOW_MAINNET=true`. Testnet is
  the default and funding mainnet from an API endpoint is refused outright.
- **A price that drifts above the approved amount re-parks for approval**
  rather than executing, governed by `PRICE_DRIFT_TOLERANCE`.

## Windows note

Smart App Control blocks the native wheels for `bitarray`, `ckzg`, and
`regex`; `ckzg` breaks `import eth_account` outright. Stubs in `infra/*_stub/`
work around this locally and must be re-copied over `site-packages` after any
venv rebuild. This makes the local signing environment non-reproducible from
`requirements.txt` — prefer WSL or the Docker image (which installs the real
wheels) for anything involving wallet signing.
