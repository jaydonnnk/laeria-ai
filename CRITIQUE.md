# laeria.ai — adversarial review

Reviewed at commit `3e399e7`. 38 commits, ~9,900 lines. Read: all backend services, agents, routes, schema, infra, docs, and the frontend surface.

---

## 0. The one that ends the conversation

**Your data source stopped existing this month.**

`services/reddit.py` is built entirely on unauthenticated `old.reddit.com` HTML, fetched with a spoofed Chrome user-agent because — your own docstring says it — "a bot-style UA gets 403."

On **June 30, 2026**, Reddit announced that old.reddit.com will require login, rolling out "over the next month." Today is July 27. The stated reason, from a Reddit admin, is that the logged-out old interface is "a significant source of abusive scraping and automated traffic." That is a description of your code. Reddit's May 2026 policy update named unauthorized scraping as a Rule 8 violation, and the March 2026 Responsible Builder Policy requires approval before API access and prohibits unapproved commercialization.

Your file header notes markup "verified live 2026-07-11" — eleven days *after* the announcement. You verified the selectors and missed the eviction notice.

There is precedent and it is not encouraging. **GummySearch — the category-leading Reddit research product, with paying customers and years of operating history — shut down commercial operations after Reddit denied its API access.** They asked permission and were told no.

The fallback in your docstring is "swap to a sanctioned third-party provider (Apify/Data365/etc.)." Those are resellers of the same scraping, carrying the same terms-of-service exposure, and they charge per request. Your unit economics currently assume the data is free. It is not free and it is not sanctioned — the access path you rely on is being closed, and every sanctioned alternative has a price attached.

> **Update, 2026-08-03.** A Data API application was filed under the Responsible Builder Policy and **denied** the same day, citing non-compliance and/or missing detail without specifying which. The application disclosed the current logged-out HTML access. A reply asking which element was at fault is the remaining free option; the questions below stand unchanged.

**Everything below this line is subordinate to this problem.** You have a research product with no legal, durable, affordable path to its only input.

---

## 1. Architecture

### 1.1 The pacing lock is a hard throughput ceiling on the entire product

```python
_pace_lock = threading.Lock()   # CLASS-level
_last_request_at = 0.0          # CLASS-level
_MIN_REQUEST_GAP_SECONDS = 1.5
```

This is process-global. Every `RedditService` instance in the process shares one 1.5-second gate. That's a deliberate choice and it's correct for politeness — and it means **the ceiling for your entire deployed service, across all users, forever, is 40 Reddit requests per minute.**

Cost per operation, counted from your own code:

| Mode | Requests | Forced sleep |
|---|---|---|
| Mode 2 (`synthesise_decision`) | 4 subs × 3 queries = 12 searches + 8 thread fetches = **20** | **30s** |
| Mode 1 (`mine_retrospectiveRFs`) | 4 subs × 4 templates = 16 + 3 site-wide + 8 fetches = **27** | **40.5s** |

Two Mode-2 queries per minute. Total. That is not a rate limit you tune later — it's the shape of the system. Ten simultaneous users means the tenth waits five minutes in the lock before their own 30 seconds begins.

### 1.2 Long-running work is done inside the HTTP request

`POST /research/retrospective` — your own docstring: **"Synchronous (2-4 minutes)."**

There is no job queue, no task table, no polling endpoint. The comment says "async job + status polling is a later refinement." It isn't a refinement; it's the thing that makes the endpoint callable. Cloudflare's default proxy timeout is 100 seconds. Most PaaS ingress layers cut somewhere between 30 and 120. Your Mode 1 endpoint will 504 in production and the user will see a generic error while your server burns three minutes of paid LLM calls into a socket nobody is listening to.

FastAPI's threadpool (40 workers, anyio default) doesn't save you either — those 40 threads all queue on the same 1.5s lock.

### 1.3 There is no cache anywhere

Two users ask "is the Steam Deck worth it." You do 40 Reddit requests and 6+ LLM calls, twice, for a byte-identical answer. Reddit threads about a 2023 product do not change hourly. A one-day result cache keyed on normalized query would cut your cost and latency by an order of magnitude and you have not built it.

Worse: `signal_analysis.py` writes embeddings to `thread_embeddings` "for future cross-session reuse," and **nothing in the codebase ever reads that table.** I grepped. It's write-only. You pay for the embeddings, pay for the storage, grow the table unbounded (no dedup on `thread_id`), and get nothing. That's not a cache, it's a landfill with a comment claiming it's a cache.

### 1.4 Single-tenant architecture wearing a multi-tenant costume

`core/auth.py` validates the bearer token, then:

```python
if user is None or user.id != settings.owner_user_id:
    raise HTTPException(status_code=403, detail="not the owner")
```

One user. Hardcoded UUID in `.env`. And `db/repositories.py` calls `_owner()` in **fifteen** places, because the backend runs on the service-role key and bypasses RLS entirely — so `_owner()` *is* your access control, in application code, in every query, by hand.

Meanwhile there's a `/login` page, a Supabase Auth integration, RLS policies in the schema, and a public deployment at `laeria-ai-backend.onrender.com`. You've built the entire surface of a multi-user product over a single-user core. Every repository function needs rewriting to accept a `user_id` before user #2 exists. That's not a scaling task you defer — it's the load-bearing wall.

Also: `require_owner` makes **a network call to Supabase on every single request**. You noted this ("fine at personal scale"). It's a per-request dependency on a third party in the hot path, with no JWKS caching, and it's the first thing that falls over.

### 1.5 Eighty function-level imports

Nearly every route handler opens with `from db import repositories as repo`. Eighty instances across the backend. This pattern doesn't resolve circular dependencies, it *hides* them — and it moves import errors from boot time to request time. A typo in a rarely-hit branch of `_execute_card_purchase` surfaces as a 502 in front of a user instead of a crash at deploy. It also defeats static analysis, which matters more than usual here because you have no tests to catch what the analyzer would have.

---

## 2. The mandate is not enforcing what you think it's enforcing

This is the part of the codebase you're proudest of. The comments call it "defense in depth," "three verification layers," "the real safety logic." It has a structural hole.

### 2.1 The approved amount is never the ceiling

`api/routes/actions.py`, `_execute_card_purchase`:

```python
actual = max(dom_price, float(action.get("amount_usd") or 0))
...
headrooms = [float(actual) * 1.05 + get_settings().checkout_shipping_buffer_usd]
if mandate_now.max_per_transaction > 0: headrooms.append(mandate_now.max_per_transaction)
if mandate_now.max_per_month > 0:       headrooms.append(...)
ceiling = round(max(min(headrooms), 0.0), 2)
```

`action["amount_usd"]` is the price the human saw and approved. `dom_price` is the price right now. You take the **max**, then build the ceiling from *that*, then check the live checkout total against *that*.

So all three of your "verification layers" verify the current price against the current price. **The number the human actually consented to is never used as a bound.** A human approves a $50 purchase; the price moves to $400 inside the window; the agent buys at $400 as long as it fits the standing mandate cap. The x402 path (`_execute`) has the identical structure — it re-discovers the price and re-checks against the *new* number.

The approval window is 10 minutes for direct proposals, which limits real-world exposure. But `_propose_alert_action` in the monitor worker creates actions with a **24-hour** window. And the invariant is simply wrong regardless of the window size: consent was given for an amount, and the amount is not enforced.

Fix is three lines: `ceiling = min(ceiling, approved_amount * (1 + tolerance))`, and re-park for approval if the price moved beyond tolerance.

### 2.2 Zero means unlimited, in three fields at once

```python
class ActionMandate(BaseModel):
    max_per_transaction: float = 0.0
    max_per_month: float = 0.0
    require_confirmation_above: float = 0.0
    autonomous_actions_enabled: bool = False
```

And every check is guarded `if mandate.max_per_transaction > 0`. So a mandate with all fields at their defaults means: **no per-transaction cap, no monthly cap, no confirmation threshold.** Combined with `autonomous_actions_enabled = True`, that is unbounded autonomous spending.

`get_mandate()` returns `{}` when the profile row doesn't exist. `ActionMandate(**{})` gives you exactly that all-zeros object. The *only* thing standing between "no profile row" and "unlimited autonomous spend" is `autonomous_actions_enabled` defaulting to False.

In a financial control system, an unset limit must mean **deny**, not **allow**. Use `float | None = None` and treat `None` as zero-allowance. This is the most common way spending controls fail in the wild and you've reproduced it faithfully.

### 2.3 The execution-time recheck is mostly ceremonial

```python
recheck_mandate = _mandate().model_copy(update={"allowed_categories": []})
svc.verify_within_mandate(amount_usd=actual, category="", mandate=recheck_mandate, ...)
```

You blank the categories, pass an empty category, and omit `vendor` entirely (so `if vendor and vendor in mandate.blocked_vendors` is always False). The comment justifies this: *"category/vendor were checked at propose and cannot have changed."*

They can change. `PUT /actions/mandate` is a live endpoint. A user who adds a vendor to `blocked_vendors` during a 24-hour approval window will still have the action execute. The comment states a false invariant with total confidence, which is a pattern I'll return to.

Net: with default caps at zero, the entire execution-time recheck is a DB round-trip that can raise nothing.

### 2.4 The financial ceiling is enforced by regex over rendered HTML

```python
m = re.search(label_pattern + r"[^$]*\$\s*([\d,]+\.?\d*)", text, re.IGNORECASE)
```

You are scraping the order total out of a third party's rendered checkout page text and using it as the authoritative number for a money gate. The `\$` is hardcoded — any non-USD store returns `None`, which fails safe but means the product simply doesn't function outside USD. Any Shopify A/B test, locale change, or theme update either breaks it (safe) or matches the wrong figure (not safe).

This is the correct approach *for a demo* and structurally unshippable for real money. Real agentic commerce rails exist specifically so nobody has to do this — see §5.

### 2.5 Fifty-six bare `except Exception`

In a codebase that moves money. Most log at `warning`, several at `debug`. When `issuer.cancel()` fails, you log at `error` and continue — leaving a **live virtual card with a real spending limit** attached to nothing, where the only record is a log line on an ephemeral container. `_persist_embeddings` swallows at `debug`. The philosophy is "best-effort, never break a feature," and applied uniformly it means a payments system where any component can fail completely and silently.

### 2.6 Zero tests

`tests/` contains one file: `test_environment.py`, a 116-line connectivity script. Your README claims `tests/  Environment + unit tests`. There are no unit tests. Not one.

The git log contains: `1b796cf Tester audit: fix mandate-bypass bug + 3 smaller fixes`. You have already shipped a mandate bypass, found it by manual audit, and **added no regression test.** I found another one (§2.1). The pattern will continue, because you have no mechanism that would catch it.

`verify_within_mandate` is a pure function with six inputs and a well-defined truth table. It is the single most testable function in the repository and the one whose failure costs real money. It has zero coverage.

---

## 3. Security

- **Two private keys in plaintext env vars.** `X402_AGENT_PRIVATE_KEY` and `X402_TREASURY_PRIVATE_KEY` on a Render instance. Testnet today; `config.py` says the production path is "flip `X402_NETWORK` to Base mainnet and fund the agent wallet — same code either way." It is not the same. There's no KMS, no HSM, no key rotation, no multisig, no on-chain velocity limit. The *only* thing bounding real spend is the application-layer mandate logic in §2, which has already had one bypass and has no tests.

- **`wallet.py` refuses mainnet transfers. `payment.py` doesn't.** `fund_agent` blocks `eip155:8453` with a good comment ("funding real money is a human act, not an endpoint"). But `_signer_client` unconditionally registers `eip155:8453` for signing, so `pay_402` will happily sign mainnet payments. Half the guard rail.

- **`thread_embeddings` has no `user_id` and no RLS.** It's the one table excluded from the `enable row level security` block in `schema.sql`. With the anon key, it's readable. It's low-value data, but it's the tell that the tenancy model wasn't applied systematically.

- **`obsidian_verify_ssl: bool = False` by default.** Localhost with a self-signed cert makes this defensible; a config default of "don't verify TLS" is still the kind of thing that migrates to production.

- **CORS `allow_origins=["*"]` when `CORS_ORIGINS` is unset.** Mitigated by bearer-token auth (you note this), and the deployed instance may or may not have it set — a wildcard default on a public deployment is a coin flip you didn't need to take.

- **You committed a stub that disables a cryptography library.** `infra/ckzg_stub/` replaces `ckzg` with functions that raise `NotImplementedError`, to route around Windows Smart App Control blocking the DLL. Your reasoning is sound — KZG is only used for EIP-4844 blobs, which you never create. But the operational instruction is "copy this package over `.venv/Lib/site-packages/ckzg*` after any venv rebuild," meaning your local signing environment is a hand-patched venv whose state isn't reproducible from `requirements.txt` and isn't verifiable by anyone. `SESSION_TRANSFER.md` confirms this breaks repeatedly. For a wallet-signing codebase, "manually overwrite a crypto package in site-packages" is not a workaround, it's an unmanaged risk. Use WSL or a container locally, like your Dockerfile already does for prod.

---

## 4. Where the honesty gap is

The code comments are, in places, better written than the code. That's a specific failure mode, not a compliment: prose confidence is doing work that verification should be doing.

- **`README.md` says: "This repository is the Phase 0.1 scaffold: structure, config, and skeletons only. No feature logic is implemented yet."** Thirty commits and a full payment pipeline later, this is the first thing any investor or engineer reads. It is spectacularly wrong about your own project.

- **`SESSION_TRANSFER.md` says the payment lifecycle was "validated end to end."** What `checkout.py` actually does under the default `bogus` profile: enters the magic PAN `"1"` into Shopify's Bogus Gateway *instead of the issued card number*, then calls `stripe.test_helpers.issuing.Authorization.create/capture` to fabricate a matching transaction on the card so it "shows the real transaction." No card has ever been charged. No money has moved through the card rail. That's a legitimate demo composite — clearly documented in the source, to your credit — but "validated end to end" is not what happened, and if you put that in a pitch you will be caught.

- **The comment "category/vendor were checked at propose and cannot have changed"** is false (§2.3), stated as fact.

- **`docs/BUILD_GUIDE.md` says: "Phase 1 alone is a complete, useful product. Ship it, use it, then decide if Phase 3 is worth building."** You wrote the correct strategy for yourself and then ignored it. You are at Phase 4+ with Phase 1 never validated against real users, the frontend never deployed, and — per your own notes — the authed pages *never once rendered with real data*.

---

## 5. Market position

### 5.1 The core loop is pointed at localhost

```python
action_vendor_url: str = "http://127.0.0.1:8000/vendor/deep-report"
```

Your headline promise — Reddit reaches consensus, agent buys the thing — routes to **your own $0.01 fake report endpoint.** `research/act` passes the consensus pick through as a *description string*. The monitor worker's "order a replacement" trigger does the same: your headphones are failing, so the agent buys a $0.01 fake report from itself.

The card rail works around this by driving a Shopify dev store you own, which per your own notes **contains only snowboards** — so "a realistic consensus pick returns no matches and hits the empty state."

Neither rail can buy a thing a real person wants. That isn't a gap in polish; it's the absence of a supply side. And a supply side is not something you build — it's something you spend years on partnerships acquiring.

### 5.2 x402 is not a consumer-goods rail

Coinbase reported roughly **$50M cumulative volume across ~165M transactions** on x402 by late April 2026. That's about **$0.30 per transaction.** x402 is an API-metering and micropayment rail. CoinDesk in March 2026: "demand is just not there yet." Your own demo vendor charges $0.01 — which is a perfectly representative x402 transaction, and that's the problem. You built the payment layer that fits the protocol, then wrote a narrative about buying headphones on top of it.

### 5.3 You are between two settled markets, on the losing side of both

**Reddit research:** ChatGPT and Google answer Reddit questions from **licensed** data. GummySearch, the incumbent, is dead because Reddit denied it access. Syften survives at $19–79/mo doing keyword alerting. Your differentiator over ChatGPT is "structural anti-shill analysis and honest confidence labelling." Examine what that actually is: at most 8 threads, 10 top-level comments each, truncated to 600 characters — call it 80 comments to represent "community consensus." And `apply_signal_filters` relaxes to *keep everything* whenever fewer than 5 threads clear the bar, which is precisely the niche-topic case where Reddit beats a search engine. Your filter disengages exactly where your edge would be. The confidence labels are then generated by the same LLM reading the same thin corpus.

**Agentic commerce:** Google's AP2 launched with 60+ partners including Mastercard, PayPal, Coinbase, and Amex, built on W3C Verifiable Credentials. OpenAI's Instant Checkout has shipped inside ChatGPT via the Agentic Commerce Protocol since September 2025. Mastercard Agent Pay binds tokenized credentials to a specific agent, merchant scope, and consent policy — in the card network, cryptographically, not in a Python `if` statement. Visa shipped Intelligent Commerce Connect in April 2026. The x402 Foundation went to the Linux Foundation in April 2026 with AWS, Google, Microsoft, Stripe, Visa, and Shopify as members.

Every safety property you hand-built — spend caps, merchant scoping, consent binding, disposable credentials — is being standardized at the network layer by the people who own the rails. Your Playwright-drives-Shopify-checkout approach isn't an early version of that. It's the thing those standards exist to replace.

### 5.4 The demand assumption is untested and probably wrong

The premise: people will delegate purchasing authority to an agent whose buying decision comes from ~80 scraped Reddit comments.

Consider what a person actually does before a $300 purchase. They read Reddit *themselves*, for twenty minutes, and enjoy it. The research is not the painful part — the deliberation *is* the product for most discretionary purchases. Then, having decided, they type the name into Amazon. Fifteen seconds. Your agent removes the fifteen seconds and automates the twenty minutes people voluntarily spend.

For the purchases where automation genuinely helps — recurring consumables, commodity replacements — there is no consensus to synthesize. Nobody writes a 200-upvote thread about which paper towels to buy. **The purchases worth automating are exactly the ones with no Reddit signal, and the purchases with rich Reddit signal are exactly the ones people want to decide themselves.** That inverse relationship is the strategic core of the problem and I don't see anywhere in your docs that you've confronted it.

---

## 6. What I'd actually do

Ranked by whether it changes your odds.

1. **Resolve the data source or stop.** Two weeks, no code. Apply for Reddit API access under the Responsible Builder Policy and get a real answer. Price Apify/Data365 per request and put it in a spreadsheet against your per-query cost. If the answer is "no access, and providers cost more per query than a user would pay," you have learned the single most important fact about this business and you should learn it now rather than after the hackathon.

2. **Delete the payment rails and keep the research.** You have two half-products. The research half is differentiated, working, and yours. The payments half is a demo pointed at localhost, competing with AP2 and ACP, and it's where 100% of your security risk lives. `BUILD_GUIDE.md` already told you Phase 1 was a complete product. Listen to yourself.

3. **If the payments stay: fix §2.1 and §2.2 today.** Clamp the ceiling to the approved amount. Make unset limits mean deny. Write the truth table for `verify_within_mandate` as pytest cases. That's an afternoon and it closes the two flaws that would actually cost someone money.

4. **Fix the README.** It describes an empty scaffold. It's your front door.

5. **Ship Mode 2 to ten strangers with no payment layer at all** and watch whether anyone returns a second time. Everything in §5.4 is a hypothesis. Ten users settles it faster than another architectural phase.

---

## The honest summary

The engineering craft here is above average for a solo build — the frontend is clean and typed, the abstractions are mostly sensible, and the code is unusually well-documented. That's exactly why it's worth being blunt: the craft has been spent on a system whose input is being cut off this month, whose output is pointed at a fake vendor on localhost, whose safety layer verifies the wrong number, and whose market is being closed by consortia with hundreds of partners.

You have built a beautiful, careful, well-commented machine for a problem you haven't verified exists, using data you don't have rights to, on rails that are being standardized out from under you.

The good news is that the research engine is genuinely interesting and it doesn't need the payment layer. The bad news is that it needs a data source, and you don't have one.

---

*Sources for external claims: Reddit login requirement announced 2026-06-30 (Slashdot, Digital Trends, Legal News Feed); Reddit Responsible Builder Policy and Rule 8 scraping enforcement (redditapis.com, crawlora); GummySearch shutdown following denied API access (SubredditSignals, prems.ai); AP2 / ACP / Visa Intelligent Commerce Connect / Mastercard Agent Pay (eco.com, internet-pros, wetheflywheel); x402 volume and Linux Foundation transition (Chainalysis, CoinDesk 2026-03-11, BlockEden).*
