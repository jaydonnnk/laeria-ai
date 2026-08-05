# Session Transfer

Session of 2026-08-03 → 08-06. Started at `d08a6b4`, ends at `fa1a6c3` (13 commits, ~4,400 lines excluding fixtures). Synced — local and origin/master match.

## Completed

- **Adversarial review triaged and Phase A executed.** An external critique (`CRITIQUE.md`) was verified line by line against the code, then addressed per `docs/REMEDIATION_PLAN.md`. Two real mandate bypasses were closed: the approved amount never bounded a purchase (execution compared the live price against itself), and an unset spending cap meant *unlimited* rather than zero.
- **Reddit shutoff survived.** Reddit announced on 2026-06-30 that logged-out `old.reddit.com` access ends within the month — the app's only data source. A record/replay layer at the HTTP boundary plus a captured 63-request corpus means the demo cannot 403 on stage. Live capture still worked when taken.
- **Reddit Data API applied for and denied** (ticket 18183736, 2026-08-03), citing unspecified non-compliance and/or missing detail. No legal, durable data path exists.
- **Landing rebuilt** to lead with Reddit research rather than the payment rail, then widened from purchases to decisions generally.
- **Frontend deployed** — `https://laeria-ai.vercel.app`, all three `NEXT_PUBLIC_*` values verified correct by reading them out of the shipped bundle.
- **All five authed pages verified with real data** for the first time, desktop and mobile, via a hand-captured browser session.
- **Root cause found for intermittent request failures** that had appeared as CORS errors across three runs on different pages: stale pooled HTTP/2 connections to Supabase.
- **Research made ~2x faster and cacheable**; long research moved off the HTTP request onto a job queue.
- **Sign-up shipped and confirmed working end to end by the user**, with Google OAuth and email/password. Research modes are open to any signed-in account; payments remain owner-only.

## Decisions

- **Mandate stays the policy layer; enforcement moves later.** Kite Agent Passport was investigated in depth and maps near 1:1 onto `ActionMandate` (`max_amount_per_tx`, `max_total_amount`, `ttl_seconds`, x402 `allowed_endpoints`). Deferred rather than integrated: its docs ship as Claude Skills + a `kpass` CLI (`github.com/gokite-ai/passport-skills`), the model appears to be a *local* agent with browser approval rather than a hosted service acting for many users, and the Windows install is a plausible day-loss given Smart App Control history.
- **Payments owner-only, research multi-user.** The agent wallet key, Stripe cardholder and storefront session are single global instances; data scoping cannot separate them, so only the auth level can.
- **Corpus replay over a logged-in scraper.** Logged-in `old.reddit` would still work, but it is the exact behaviour Reddit's policy names and risks an account ban.
- **Disclosed current scraping in the API application.** Concealing it would violate the policy outright and risk permanent revocation; the disclosure was probably decisive in the denial, and the same call would be made again.
- **`/research` kept its name** while `/decision` became "Worth it?". Its copy was already decision-general and "How it went" is lifted verbatim from its own classifier prompt — renaming would have been churn.
- **Fuji deprioritised.** Base Sepolia is the default network; Fuji was an Avalanche flourish and its faucet is the one genuinely needing a sponsor coupon.
- **x402 payments are gasless for the payer** (EIP-3009, facilitator submits), so the agent's existing $19.94 USDC is enough. Only `wallet.fund_agent` needs gas — reframing faucets from blocking to optional.

## Traps

- **Binding a user in a FastAPI dependency does not work.** A ContextVar set there never reaches the endpoint — every threadpool dispatch copies the context. Unit tests that call the dependency and the repository in one context confirm the wrong thing and pass. Verified empirically: the endpoint reads the default. `BaseHTTPMiddleware` is equally unusable, raising *"Token was created in a different Context"*. Only raw ASGI middleware works. The temptation is to "fix" the reset; the propagation is what is broken.
- **`npx next build` while the dev server runs clobbers `.next`** and every page 404s on `/_next/static/*`. This produced three false 0/10 verification runs before being fixed. `npm run build:check` now writes elsewhere; the temptation is to assume the app broke.
- **`networkidle` never settles on a deployed Next app** (RSC prefetch keeps chattering). It timed out at 60s on a page that loads in 0.3s. The temptation is to treat the timeout as a real failure.
- **Automated page checks pass on functionally broken pages.** Mobile nav was entirely missing and scored 10/10 — no console errors, no failed requests, plenty of text. Screenshots caught it; the checks never would.
- **Never call an authenticated endpoint without a session.** `api.request()` force-redirects to `/login` on 401, so a `/me` call in `Header` — whose hooks run even when it returns `null` — threw every signed-out visitor off the landing page.
- **The OpenAI SDK defaults to a 600s timeout.** An intermittent stall left a job "running" indefinitely, indistinguishable from slow work. Diagnosed only via a thread dump.
- **Playwright fails from PowerShell** with `WinError 5` on the node driver, while succeeding from Git Bash. Fourth thing Smart App Control has broken here, after `bitarray`, `ckzg`, `regex`.
- **`save_login --refresh` used to report success over a still-expired token.** Supabase access tokens last ~1h, shorter than a working session; it now verifies the token it produced.

## Working Agreements

- The user asks "is this feasible?" and "confirm with me first" before build-heavy work, and expects an assessment with tradeoffs rather than an immediate start. Approval is explicit and per-item.
- Commits are left ready for a GUI **Sync** button, never pushed. Claude must not be added as co-author.
- The user reallocates work explicitly ("I WON'T be doing 2-4") and expects the list to be re-scoped without argument — one flag on the risk, then move on.
- Corrections are wanted plainly. When a claim is wrong, the expectation is to say so and move, not to hedge.
- Loaded language about the user's own project is unwelcome in the repo (`CRITIQUE.md`'s "it's stolen" was reworded on request).
- The user pushes back on over-narrow scoping ("not every query is a purchase") and expects the underlying code to be checked before renaming.

## Files Changed

Backend:
- `core/current_user.py:1-95` — new. ContextVar + `CurrentUserMiddleware` (raw ASGI). Docstring records why dependency and BaseHTTPMiddleware binding both fail.
- `core/auth.py:88-160` — split into `_validate` / `peek_user_id` / `require_user` / `require_owner`; token-validation cache bounded by the token's own `exp`.
- `db/repositories.py:22-35` — `_owner()` now resolves to the request's user; the 17 call sites are untouched.
- `db/client.py:1-100` — 5s keepalive expiry and a transport that replays a dead pooled connection **for GET/HEAD/OPTIONS only**, so a dropped write can't duplicate.
- `api/routes/actions.py:134-250` — approved-amount clamp with re-park on price drift, on both rails; `None` caps in headroom math; live vendor re-check.
- `api/routes/research.py:57-155` — `/act` gated to owner; `/decision/submit`, `/retrospective/submit`, `/jobs/{id}`.
- `agents/research_agent.py` — synthesis split into two parallel calls (`_VERDICT_SYSTEM` / `_SCRUTINY_SYSTEM`, `confidence` deliberately kept with `red_flags`); cache read/write; plan caching for replay.
- `services/reddit.py:105-190`, `services/reddit_fixtures.py` — record/replay incl. recorded failures.
- `services/jobs.py`, `services/research_cache.py` — new.
- `services/llm.py:17-31` — explicit 120s timeout.
- `services/payment.py:88-135`, `services/bazaar.py:23-35` — mainnet signing off unless `X402_ALLOW_MAINNET`.
- `core/models.py:121-136` — mandate caps are `float | None`; unset means zero allowance.
- `scripts/{save_login,verify_pages,capture_corpus}.py` — new tooling.
- `tests/{test_mandate,test_auth_cache,test_tenancy}.py` — 41 tests. Tenancy tests drive the real app through `TestClient` deliberately.

Frontend:
- `app/page.tsx` — landing rewritten (Reddit-led), then widened to decisions; `gate()` routes signed-out visitors to `/signup?next=`.
- `app/signup/page.tsx`, `app/auth/callback/page.tsx`, `components/GoogleButton.tsx` — new.
- `components/Header.tsx:24-60` — public-route guard around the `/me` call; owner-only nav filtering; mobile nav row.
- `lib/api.ts:8-60,110-130` — retry for reads, `runJob` submit+poll, `me()`.
- `app/commerce/page.tsx` — per-section error notices with retry, replacing one page-wide banner.
- `next.config.js:1-21`, `package.json` — `build:check` writes to `.next-check`.

## Open Work

- **Data source is unresolved and is the binding constraint.** The API application was denied; a reply to ticket 18183736 asking which element failed is drafted but unsent. Paid providers (Apify/Data365) resell the same scraping and remain unpriced. Diversifying to sources with real APIs (YouTube, Hacker News, Stack Exchange) is unexplored. Everything about the product's durability depends on this; the demo does not, because of the frozen corpus.
- **Kite integration not started.** The cheap version (adding Kite as a network via `X402_EXTRA_NETWORKS`, funding from `faucet.gokite.ai`) is unblocked. Per-user passports depend on resolving whether a hosted service can hold passports on users' behalf.
- **Faucets not done.** Both wallets show `0.000000 ETH gas`; treasury holds $0.06 USDC. This blocks only the live "Fund" step — the stepper already shows Fund complete from the agent's existing balance, and x402 payments need no gas.
- **Backend changes since the last deploy are not live on Render** — the auth cache, connection fix, job endpoints, LLM timeout and multi-tenancy all require a redeploy.
- **Background monitoring is owner-only.** `items_due_for_check()` filters by user and the worker runs unbound; a guest's on-demand "check now" works.
- **`thread_embeddings` has no `user_id` and no RLS** — the one shared table, now that other accounts exist. It is also still write-only; nothing reads it.
- **Demo-day items the user has declined:** fallback video and dress rehearsals. Demo day would be the first full run.
- **StraitsX sandbox** needs business verification, expected at the event.

---

## Prompt for New Chat

This continues work on **laeria.ai** at `C:\Users\jayd0\OneDrive\Desktop\laeria.ai` — an agent that researches decisions from Reddit community consensus and can complete purchases under a spending mandate. It is the submission for an agentic-payments hackathon (SMU, 14–16 August 2026; sponsors StraitsX, Avalanche, Kite).

Frontend is live at `https://laeria-ai.vercel.app`; backend at `https://laeria-ai-backend.onrender.com`. Everything is committed and synced at `fa1a6c3`. 41 backend tests pass and the production build is clean.

The last session responded to an adversarial code review (`CRITIQUE.md`, plan in `docs/REMEDIATION_PLAN.md`). Two mandate bypasses are closed: the approved amount now bounds a purchase and re-parks for approval if the live price drifts past tolerance, and an unset spending cap now means zero allowance rather than unlimited. Base mainnet signing is off unless explicitly enabled. Research is cached and ~2x faster, long research runs as a polled job rather than inside the HTTP request, and sign-up is live with Google and email/password — the user has confirmed both work.

Research modes are open to any signed-in account; `/commerce`, `/actions`, `/wallet`, `/store`, `/obsidian` and `/research/act` are owner-only, because the agent wallet key, Stripe cardholder and storefront session are single global instances that data scoping cannot separate.

The binding constraint is the data source. Reddit is closing logged-out `old.reddit.com` access, which is the app's only input, and a Data API application was denied on 2026-08-03. A frozen 63-request corpus (`REDDIT_SOURCE=live_then_fixture`) means the demo cannot fail on stage, but no legal durable source exists.

Several environment facts cost real time last session. A ContextVar set inside a FastAPI dependency never reaches the endpoint, so per-request user binding lives in raw ASGI middleware — `BaseHTTPMiddleware` raises "Token was created in a different Context". Running `npx next build` while the dev server is up clobbers `.next` and makes every page 404 on static chunks; `npm run build:check` exists to avoid that. Playwright works from Git Bash but fails from PowerShell with `WinError 5`. Supabase access tokens expire in about an hour, and `python -m scripts.save_login --refresh` renews a saved session without another sign-in.

Automated page checks have twice passed on functionally broken pages, so `scripts/verify_pages.py` writes screenshots that are worth looking at rather than trusting the score.

Kite Agent Passport was researched but not integrated; its delegation schema maps closely onto the existing `ActionMandate`. Faucets remain undone, which affects only the live "Fund" demo step. The user has declined to record a fallback video or run dress rehearsals.

Wait for instructions before taking any action.
