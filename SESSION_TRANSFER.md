# Session Transfer

Session of 2026-08-06. Started at `e2eab6d`, ends at `c1869e1` (11 commits, ~1,900 lines). **Not pushed** — commits are waiting for the GUI Sync button.

## Completed

- **The event's sponsor list was wrong in this repo, and the plan was built on it.** Verified against the Luma listing: the StraitsX Agentic Playground Hackathon is hosted by StraitsX with Avalanche as title partner, AWS as cloud partner, and SMU as venue. **Kite is not involved.** Kite is descoped to a slide in both `SESSION_TRANSFER` and Swap C.
- **Four judged milestones recorded** — Funding (XSGD into a KYC'd account), Discovery, Issuance, Execution. Three of the four are already built on the card rail; the gap is the funding leg.
- **The Fuji facilitator question is settled without needing DevRel.** Probing `/supported` showed Ultravioleta DAO serves x402Version 2, scheme `exact`, network `eip155:43113`, USDC `0x5425890298…` — matching what `payment.py` and `wallet.py` already expect. One env var, no account, no API key.
- **Funding token is configurable** (`STABLECOIN_CONTRACT` / `_SYMBOL` / `_DECIMALS`), so XSGD is an env change rather than a code edit. Avalanche C-Chain mainnet added with no default token. `fund_agent`'s mainnet refusal moved from a hardcoded Base chain id to the network's `testnet` flag.
- **The `/cards/test-issue` bypass is closed.** `APP_ENV=production` is now set on Render (verified: `/health` reports `production`), and the Commerce page's test-issue form is removed.
- **Browser extension built and working** — MV3, loaded unpacked, verdict overlay on product pages and order import on confirmation pages. Detection verified against three live stores.
- **`GET /research/subreddits`** added, reusing the research planner, because `POST /monitor/items` requires at least one subreddit.
- **The cause of 2–3 minute reports was found and fixed.** It was not model speed — see Traps.
- **Models benchmarked** on the real synthesis prompt and corpus.
- **`StraitsXAdapter` implemented** as a working config-driven HTTP client with 10 tests.
- **JSON salvage rewritten** — 11 tests.
- **Demo-script landmine fixed** — the pre-stage checklist would have refused every purchase on stage.
- **Fuji USDC faucet succeeded** — treasury `0x3aDe6336e45c37a41193aEcb9b02315eA59268d0` holds 20 USDC, confirmed on-chain.
- 52 backend tests pass; frontend build clean with `/decision` still prerendered static.

## Decisions

- **Kite descoped rather than integrated.** Not a partner, and two findings closed it independently: the "one env var" integration was never established (Kite documents scheme `gokite-aa` on chain 2368 while the code registers standard `exact`, and Kite is absent from Coinbase's network list), and a hosted service cannot hold per-user passports — developer mode, where the service pays and bills its customers, is the only server-shaped option.
- **Haiku 4.5 recommended over both DeepSeek and Sonnet, on spread rather than median.** The user waits on the slower of two parallel synthesis halves, so tail latency costs more than mean. `deepseek-v4-pro` was explicitly rejected: erratic (16.6–50.0s) and it found **zero** red flags on a corpus where every other model found 2–4. The user chose to test DeepSeek first with the timeout fix applied, isolating the two changes.
- **Reddit request count left alone** at the user's choice — cutting searches would worsen the thin-coverage problem for ~5s against an LLM that varies by 30s.
- **The extension is a thin client.** `content.js` holds no credentials; `background.js` is the only place with a token, which also means MV3 `host_permissions` cover its fetches and no backend CORS change was needed.
- **Not submitted to the Chrome Web Store.** Review takes weeks and gates nothing for a demo.
- **The demo runs locally, not from the deployed stack**, because `BROWSER_HEADED=true` only means something when the browser is on the presenter's screen — the visible checkout is the strongest beat and is invisible in a Render container.
- **`CARD_ISSUER=mock` accepted as the demo issuer.** Stripe Issuing is not enabled on the account, and Stripe's live local issuance covers 22 countries not including Singapore. This is reframed as the argument for StraitsX rather than a workaround.

## Traps

- **`:host` rules lose to page CSS.** The shadow host lives in the page's DOM, so a shop's stylesheet outranks `:host { all: initial }`. The overlay was `display: none` on the demo store while working on two other Shopify sites. Only inline styles set with `!important` survive. The temptation is to debug detection — detection was fine every time.
- **`OPENROUTER_MODEL` in `.env` overrides the code default.** Production was running `deepseek/deepseek-v4-flash`, not the `anthropic/claude-sonnet-4.5` in `config.py`. Profiling attributed to "Sonnet" for some time was DeepSeek. Print `get_settings().openrouter_model` rather than reading the file.
- **Client retries multiply the timeout.** `max_retries=2` against a 120s timeout is a six-minute worst case for one call, while `_synthesise` abandoned the half at 150s. A model with a good median and a bad tail (287s observed) therefore produced slow half-briefs. Any caller-side deadline must derive from `timeout × attempts`, not be guessed separately.
- **A greedy `\{.*\}` cannot salvage malformed JSON.** Spanning the first brace to the last reproduces a stray leading brace, failing on exactly the input it exists to rescue.
- **A monthly cap of 0 refuses everything.** `DEMO_SCRIPT.md` told the presenter to set it, which was correct before Phase A. The temptation is to read "0" as "no cap".
- **Overlapping paced requests only pays when latency exceeds the gap.** The old `_pace` slept the remainder, so latency already counted toward it; measured saving is 0.0s at 1.5s latency and 20s at 4.0s. Do not claim it as the fix for slowness.
- **A product title is not a Reddit query.** Marketing titles match almost no thread, which triggers the no-candidates retry — roughly double the time for a low-confidence answer about nothing.
- **Shopify emits `ProductGroup`, not `Product`,** for anything with variants, which is most of a real catalogue.
- **The demo store is Shopify's fictional sample catalogue.** Every research verdict against it is legitimately low-confidence, which looks identical to the pipeline failing.
- **Bash heredocs in this environment mangle `\\` to `\`.** A regex test appeared to fail until the assertion was re-run against the real source file. Test the shipped file, not a retyped copy.

## Working Agreements

- The user asks for numbered, plainly-explained steps and says so when an explanation is too compressed.
- Measurement is expected over speculation. Answers like "would model X be faster?" are wanted as benchmarks, not opinions.
- "Be brutal about it" is meant literally — a proposal's fatal flaw is wanted before its merits.
- The user challenges premises ("is there any point in doing this instead of the real site?") and expects the reasoning, not restated instructions.
- Corrections to Claude's own earlier claims are expected to be stated plainly and immediately, without hedging.

## Files Changed

Backend:
- `core/config.py:24-46` — `openrouter_model` default → Haiku 4.5; `llm_timeout_seconds` 120→45; new `llm_max_retries`. `:91-113` — `stablecoin_contract/_symbol/_decimals`. `:120-142` — StraitsX settings block.
- `services/llm.py:17-40` — retries from settings, `worst_case_seconds` exposed. `:150-205` — `_balanced_objects()` scanner replacing the greedy regex in `_parse_json_lenient`.
- `services/wallet.py:1-100` — network table gains `token`/`token_symbol`/`token_decimals`/`testnet`; Avalanche mainnet entry with no default token. `:75-95` — env overrides + `_units`. `:130-190` — `fund_agent` refuses off the `testnet` flag; response carries `token_symbol`; legacy `usdc` keys retained.
- `services/cards.py:247-400` — `StraitsXAdapter` implemented; `_first()` tolerant field reader.
- `services/reddit.py:96-115` — `_pace` reserves a slot and sleeps outside the lock.
- `agents/research_agent.py:33-40` — `_FETCH_CONCURRENCY`. `:230-250` — searches run through a pool. `:281-301` — new `_fetch_threads`. `:390` — synthesis deadline derives from `worst_case_seconds`.
- `api/routes/research.py:88-112` — new `GET /research/subreddits`.
- `tests/test_straitsx_adapter.py`, `tests/test_json_salvage.py` — new, 21 tests.

Frontend:
- `app/decision/page.tsx:52-95` — `run` split into `runQuery`; `?q=` read from `window.location` and auto-run.
- `app/commerce/page.tsx:365-380` — token symbol from balances. `:516-560` — test-issue form removed.
- `lib/api.ts:271-275` — `testIssueCard` removed. `:305-335` — `token_*` fields added to `WalletBalances`.

Extension (new): `manifest.json`, `background.js`, `content.js`, `config.js`, `popup.html`, `popup.js`, `README.md`.

Docs: `HACKATHON_SWAP.md` — Swap A facilitator resolved, Swap C descoped. `DEMO_SCRIPT.md:6` — monthly-cap instruction corrected.

## Open Work

- **The dress run is incomplete.** It reached step 11 (extension verdict) after three blocking bugs were fixed mid-run. Steps 12–19 — agent verify, propose, approve, Playwright checkout, order import, and the four proof checks — have never been executed in any configuration. This is the largest untested surface and it covers three of the four judged milestones.
- **The store catalogue blocks the approval beat.** 13 products: `selling-plans-ski-wax` at $24.95, then nothing until $600. No product sits in the $30–50 band, and all are Shopify's fictional sample data, so no product in the store can produce a high-confidence verdict. Adding real products would resolve both at once; the dress run can proceed meanwhile with ask-first lowered to $20.
- **DeepSeek timing results were never collected.** The choice between DeepSeek and Haiku depends on them. `LLM_TIMEOUT_SECONDS`, `LLM_MAX_RETRIES` and `OPENROUTER_MODEL` on Render have not been confirmed set.
- **The `?q=` full-report link still re-researches** rather than hitting the 24h cache. Render's ephemeral disk wiping `backend/.cache/research` is the leading suspicion, untested.
- **Order import is untested.** The Shopify thank-you selectors are guesses; the JSON-LD `Order` path is the durable one. Depends on the dress run reaching step 16.
- **Fuji AVAX is blocked by a circular gate** — the faucet wants a coupon or a nonzero mainnet balance, and Avalanche Support requires a mainnet balance before issuing a coupon. This blocks only `wallet.fund_agent`; x402 payments are gasless via the facilitator.
- **The extension's "full report" and "open monitor" links are hardcoded to `laeria-ai.vercel.app`** rather than following the configured backend, so they point at production during a local demo.
- Unchanged from before: `thread_embeddings` has no `user_id` and no RLS; background monitoring is owner-only; the Reddit data source remains unresolved.

---

## Prompt for New Chat

This continues work on **laeria.ai** at `C:\Users\jayd0\OneDrive\Desktop\laeria.ai` — an agent that researches decisions from Reddit community consensus and completes purchases under a spending mandate. It is the submission for the **StraitsX Agentic Playground Hackathon** (Singapore, 14 Aug 1800 – 16 Aug 1300 2026, submission Sunday 1100). StraitsX hosts; Avalanche is title partner. **Kite is not involved** — an earlier version of this document wrongly listed it as a sponsor, and a week of planning was built on that error before it was caught.

The judged flow is four milestones: Funding (XSGD into a KYC'd account), Discovery, Issuance, Execution. Discovery, Issuance and Execution are already built on the card rail. The gap is Funding, which wants XSGD on Avalanche rather than USDC on Base Sepolia. Reddit research is not a judged milestone, which makes the unresolved data source far less threatening to the submission than to the business.

Everything is committed at `c1869e1` and **unpushed**. 52 backend tests pass. Frontend builds clean.

This session fixed the cause of 2–3 minute research reports: not model speed, but `max_retries=2` against a 120s timeout multiplying into a six-minute worst case, while the synthesis deadline gave up at 150s. Timeout is now 45s with one retry, and the deadline derives from the client's own worst case. `OPENROUTER_MODEL` in `.env` overrides the code default — production was on `deepseek/deepseek-v4-flash`, not Sonnet. Benchmarked on the real prompt: Haiku 4.5 22.8–24.6s with 4 red flags, deepseek-v4-flash 17.3–18.6s with 2, deepseek-v4-pro 16.6–50.0s with **zero**, sonnet-4.5 35.0–47.1s with 3. The user opted to test DeepSeek first with the timeout fix alone.

A browser extension now exists in `extension/`, loaded unpacked. It puts a verdict pill on product pages and offers to import order confirmations into monitoring. It is a thin client — `content.js` holds no credentials, `background.js` owns the token. Three bugs were fixed during the first live test: the overlay was hidden because `:host` rules lose to the shop's own CSS (inline `!important` styles now), product titles were being sent as Reddit queries verbatim (a `cleanQuery` step now cuts them down), and malformed model JSON with a stray leading brace killed a run (the salvage path is now a brace-balanced scanner rather than a greedy regex).

`StraitsXAdapter` is a working config-driven HTTP client rather than a stub — every unknown about their sandbox is an env var, and responses are read through a tolerant field reader that accepts several plausible spellings. Stripe Issuing turned out not to be enabled on the account, and Stripe's live local issuance covers 22 countries not including Singapore, so `CARD_ISSUER=mock` is the demo issuer. That is now the argument for StraitsX rather than a workaround.

The Avalanche facilitator question was settled by probing rather than asking: Ultravioleta DAO at `https://facilitator.ultravioletadao.xyz` serves x402Version 2, scheme `exact`, network `eip155:43113` with the same USDC contract the code already has. Fuji USDC is funded (20 in treasury); Fuji AVAX is blocked by a circular gate and affects only the live Fund step.

The demo runs **locally** — backend on `localhost:8000`, frontend on `localhost:3000`, `BROWSER_HEADED=true` — because the audience watching the agent drive checkout is invisible in a Render container. A dress run following `docs/DRESS_RUN` steps in the prior conversation reached step 11 of 19 before stopping; the checkout, order import and proof checks have never run end to end. The demo store holds 13 fictional Shopify sample products with nothing between $24.95 and $600, so no product in it can yield a high-confidence verdict, and none sits in the $30–50 band the approval beat wants.

`docs/DEMO_SCRIPT.md` previously instructed setting the monthly cap to 0, which after Phase A means zero allowance and would have refused every purchase on stage. That is corrected.

Wait for instructions before taking any action.
