# Remediation plan — response to the adversarial review

Drafted 2026-07-27. Review was against `3e399e7`.

## Framing: the review is right about the code and wrong about the calendar

The review evaluates laeria as a startup. For the next 18 days it is a hackathon
submission (SMU, Aug 14–16; sponsors StraitsX, Avalanche, Kite). Two of its five
recommendations invert under that constraint:

- **"Delete the payment rails and keep the research."** Correct for a company.
  Wrong until Aug 16. The sponsors are a stablecoin issuer, an agent-payments L1,
  and the chain both settle on. Removing the payment rail submits a Reddit search
  tool to an agentic-payments hackathon. Revisit Aug 17.
- **"Ship Mode 2 to ten strangers."** Correct, and it should happen — after the
  event, not instead of preparing for it.

The rest of the review lands, and one finding is *more* urgent than it argues:

**§0 is not only a business risk, it is a stage risk.** Reddit's logged-out
old.reddit shutoff is rolling out across exactly the window containing demo day.
If it lands mid-demo the product returns 403 in front of judges. That converts
§0 from "resolve within two weeks" to "must be de-risked this week."

Priority order below is by *risk to the next 18 days*, then by risk to the
business.

---

## Phase A — this week (demo survival + the two money bugs)

Everything here is small, and all of it is on the critical path.

### A1. Make the data source survivable — **do this first**

Two independent problems, one fix each. Do not conflate them.

**A1a. Freeze a demo corpus.** Pre-fetch the threads for 3–5 demo queries now,
while logged-out access still works, and commit them as fixtures. Add a
`REDDIT_SOURCE` setting: `live` | `fixture` | `live_then_fixture`. Demo runs on
`live_then_fixture` — real when it works, silent fallback when it doesn't.

This is not a cheat. A deterministic demo is *better* than a live one: it removes
stage risk, it removes the 30–40s pacing wait from the judges' experience, and
every serious demo does it. Label it honestly in the README ("demo runs against a
frozen corpus captured YYYY-MM-DD; live fetch is the same code path").

**A1b. File the Reddit Data API application today.** Not this week — today. The
latency on the answer is weeks and it is the single most important fact about the
business. Apply under the Responsible Builder Policy as a personal,
non-commercial research build. GummySearch was denied as an established
*commercial* product; that is a different application and a different answer, so
do not pre-concede it.

Note honestly: logged-in old.reddit keeps working, so a session-cookie scraper
would technically survive. Do not build that. It is the exact behaviour Reddit
named, it is a Rule 8 violation, and it puts an account ban between you and your
data. It is not a plan.

**Effort:** A1a half a day. A1b one hour.

### A2. Clamp the ceiling to the amount the human approved

`api/routes/actions.py:137`. Currently:

```python
actual = max(dom_price, float(action.get("amount_usd") or 0))
```

The approved amount is used as a *floor*, never a ceiling. Every downstream
"verification layer" then checks the current price against the current price.

**Do not just replace `max()` with the approved amount** — the comment above it
is describing a real case (subscription selling plans expose a plan price in
`og:price` that is lower than what the cart charges). `max()` is correct for
determining what will actually be charged. The bug is that nothing then compares
that number to what was consented to.

Fix shape:

```python
approved = float(action.get("amount_usd") or 0)
actual = max(dom_price, approved)          # true charge — keep this

tolerance = get_settings().price_drift_tolerance   # e.g. 0.05
if approved > 0 and actual > approved * (1 + tolerance):
    # consent was for `approved`; the price moved past tolerance.
    # re-park for approval instead of executing.
    repo.update_action(action_id, {"status": "needs_approval", "metadata": {
        **meta, "reprice": {"approved": approved, "now": actual}}})
    raise HTTPException(409, f"price moved ${approved:.2f} → ${actual:.2f}; re-approval required")

ceiling = min(ceiling, approved * (1 + tolerance))
```

Apply the identical change to the x402 `_execute` path, which has the same
structure.

This is also a *demo asset*, not just a fix: "the price moved, so it stopped and
asked again" is a better on-stage beat than the refusal you already show, because
it demonstrates consent binding rather than a static cap.

**Effort:** 1–2 hours including the x402 path.

### A3. Unset limit must mean deny

`core/models.py:122-127`. `0.0` currently means "no cap" in three fields at once,
and `get_mandate()` returns `{}` for a missing profile row, so
`ActionMandate(**{})` is an all-zeros object = unbounded.

```python
max_per_transaction: float | None = None
max_per_month: float | None = None
require_confirmation_above: float | None = None
```

Then in `services/payment.py:62-82`, `None` means zero allowance, not infinite:

```python
if mandate.max_per_transaction is None or amount_usd > mandate.max_per_transaction:
    raise MandateViolation(...)
```

Same treatment in the `headrooms` construction at `actions.py:162-171`.

The frontend mandate form needs a matching pass so an empty field submits `null`
and renders as "no allowance", not "unlimited".

**Effort:** 2–3 hours including the frontend.

### A4. Write the mandate truth table as tests

`verify_within_mandate` is a pure function, six inputs, and the most expensive
possible failure. A mandate bypass has already shipped once (`1b796cf`) and A2 is
a second one. There is no mechanism that would catch a third.

Minimum viable suite (`backend/tests/test_mandate.py`):

- each cap: under / exactly at / over
- `None` on each cap → denied
- `autonomous_actions_enabled=False` → denied regardless of amount
- monthly cap with prior spend → boundary at the remainder
- `require_confirmation_above` → parks rather than executes
- **A2 regression: approved $50, live price $400 → does not execute**
- **A3 regression: `ActionMandate()` with defaults → denies everything**

This is the smallest amount of testing that would have caught both known bugs.
Do not attempt broader coverage before the hackathon.

**Effort:** 2–3 hours.

### A5. Close the x402 mainnet asymmetry

`services/wallet.py:134` refuses mainnet funding. `services/payment.py:106`
registers `eip155:8453` in the signer unconditionally, so `pay_402` will sign
mainnet payments. Half a guard rail is not a guard rail.

Gate the signer on the configured network the same way funding is gated. One
line, and it removes the worst-case outcome (real money moving through code that
has never had a test).

**Effort:** 15 minutes.

### A6. Fix the README

Line 11 says "This repository is the Phase 0.1 scaffold... No feature logic is
implemented yet." It is the first thing a judge or engineer reads and it is
wrong by about 9,000 lines. Replace with what the system does, what is real, and
what is demo-composited (see A7).

**Effort:** 30 minutes.

### A7. Honesty pass on the claims

`SESSION_TRANSFER.md` says the payment lifecycle was "validated end to end." What
actually happens under the default bogus profile: the magic PAN `1` goes into
Shopify's Bogus Gateway, then `stripe.test_helpers.issuing.Authorization`
fabricates a matching transaction on the issued card. No card has been charged.

The source documents this clearly — that is to your credit and the composite is a
legitimate demo technique. But "validated end to end" is not what happened, and a
judge who asks one follow-up question will find that out from you or find it out
themselves. Say it first: *"card rail is Stripe Issuing test mode plus Shopify's
bogus gateway; the authorization on the card is real, the merchant charge is
simulated."* Stating the seam yourself reads as rigour. Being caught on it reads
as the opposite.

Same pass on the false invariant comment at `actions.py:139` ("category/vendor
were checked at propose and cannot have changed" — `PUT /actions/mandate` is
live, so they can).

**Effort:** 1 hour.

---

## Phase B — before Aug 13

### B1. Async job + polling for the long modes

`POST /research/retrospective` is 2–4 minutes inside the request. Cloudflare cuts
at 100s; most PaaS ingress at 30–120s. This will 504 in production while the
server burns LLM spend into a dead socket.

Minimum: a `jobs` table, `202 + job_id` on submit, `GET /jobs/{id}` for polling,
worker runs the existing function. The frontend already has loading states to
hang this off.

If time is short, the cheaper mitigation is to run Mode 1 against the frozen
corpus only (A1a), which drops it under the timeout. Ugly, but it is the
difference between a working demo and a 504.

**Effort:** 1 day proper, 1 hour for the corpus-only shortcut.

### B2. Result cache

One-day TTL keyed on normalized query. Cuts the 40-request / 6-LLM-call cost to
zero on repeat, which matters most during rehearsals and during a demo where a
judge asks you to run the same query twice.

While in there: either wire `thread_embeddings` to actually be read on the
cross-session path, or drop the table. Right now it is paid-for, unbounded, and
never queried — no dedup on `thread_id`, no reader anywhere in the codebase.

**Effort:** half a day.

### B3. Demo hardening

- Record the fallback video (already on your open-work list). With A1a in place,
  record against the frozen corpus so the video and the live demo match.
- Two dress rehearsals, full run, timed.
- The authed pages have still never been rendered with real data. That has to
  happen before a judge sees them. It needs a Supabase login — schedule it.
- Frontend deploy to Vercel. Confirm the `NEXT_PUBLIC_*` values are actually
  correct this time; the placeholder fallback means a green build no longer
  proves anything.

### B4. Pick the pitch framing

The review's §5.4 is the sharpest strategic point in it and you should have an
answer ready, because a good judge will ask a version of it:

> The purchases worth automating have no Reddit signal; the purchases with rich
> Reddit signal are ones people want to decide themselves.

Do not hand-wave this. The strongest available answer is that laeria targets the
band where the decision is *high-stakes enough to research but low-frequency
enough that you have no expertise* — the $150–600 considered purchase you make
once every few years. That is a real band and it is where the twenty-minute
Reddit dive actually happens. Whether it is a business is untested. Say that it
is untested. Judges respond far better to a named open question than to a
confident answer that collapses under one follow-up.

---

## Phase C — after Aug 16

Ordered by whether it changes the odds, per the review.

1. **Data source resolution.** By then the API application has an answer and the
   old.reddit shutoff has fully landed. Price Apify/Data365 per request against
   per-query cost in a spreadsheet. If there is no legal source at a price a user
   would pay, that is the most important fact about this business and it is
   better learned in August than in December.
2. **Ten users, Mode 2 only, no payment layer.** Settles §5.4 empirically. Faster
   than any further architecture.
3. **Multi-tenancy**, if 1 and 2 come back positive. `_owner()` in 15 repository
   call sites and a service-role key that bypasses RLS is a load-bearing wall,
   not a scaling task. Also: `require_owner` makes a Supabase network call per
   request with no JWKS caching.
4. **Throughput.** The class-level 1.5s pacing lock caps the entire process at 40
   Reddit requests/minute — two Mode-2 queries per minute, all users, total. Only
   worth solving once the data source is legal and someone is waiting.
5. **The 60 bare excepts** and the 21 function-level `repositories` imports.
   Genuine debt, zero demo impact. The one worth fixing early is the silent
   `issuer.cancel()` failure — that leaves a live card with a real limit attached
   to nothing, recorded only in a log line on an ephemeral container.

---

## What this plan deliberately does not do

- **Does not delete the payment rails.** Wrong move before Aug 16 (sponsor fit).
  Reassess Aug 17 on the merits, when the review's argument is strong.
- **Does not fix the localhost vendor or the snowboard-only store.** A supply
  side is a partnerships problem, not a sprint. For the demo, the honest framing
  is that the storefront is a controlled environment, stated up front.
- **Does not attempt broad test coverage.** Only the mandate truth table. Every
  other test is a worse use of the remaining days.
- **Does not touch the architecture items in Phase C.** They are correctly
  identified and none of them change what happens on Aug 16.

## Order of work

```
Today      A1b (API application — one hour, unblocks nothing, delays everything if skipped)
Day 1      A1a frozen corpus, A5 mainnet gate, A6 README
Day 2      A2 approved-amount clamp, A3 None-means-deny
Day 3      A4 mandate tests, A7 honesty pass
Day 4-6    B1 async jobs, B2 cache
Day 7-9    B3 deploy + authed pages + rehearsal 1
Day 10+    B4 pitch, rehearsal 2, buffer
```

A2 and A3 are the two that would cost someone real money. If everything else
slips, those ship.
