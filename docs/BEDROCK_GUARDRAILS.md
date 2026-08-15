# Amazon Bedrock Guardrails — the safety boundary around the agent

Laeria's thinking is done by OpenRouter and is unchanged by this work. Bedrock
is not an inference provider here. It answers exactly one question, at every
point where text crosses a trust boundary:

> Can this content safely enter or leave the agent?

That question is separate from every other check the system already makes, and
it is asked by a different vendor from the one doing the reasoning — which is
the point. A model cannot be the only thing deciding whether its own inputs are
trustworthy.

| Layer | Question it answers | What it does NOT do |
|---|---|---|
| **Bedrock Guardrails** | Can this content safely enter or leave the agent? | Judge whether a recommendation is *correct*, or authorise spending |
| Relevance screen | Is this thread even about the question? | Judge safety |
| Evidence engine | Does the evidence support the recommendation? | Read content for attacks |
| Confidence ceilings | How confident is Laeria allowed to be? | Decide what to buy |
| Mandate | Is the agent authorised to spend this, here? | Read content at all |
| Payment layer | Execute only the authorised payment. | Make decisions |

None of these replaces another. Bedrock decides what may be **considered**; the
mandate still decides what may be **spent**. A guardrail verdict has never
raised a confidence level, chosen a product, or approved a payment, and the
code has no path by which it could.

Implementation: `backend/services/bedrock_guardrails.py`. Call sites:
`backend/agents/research_agent.py`, `backend/agents/shopping_agent.py`.
Tests: `backend/tests/test_bedrock_guardrails.py`.

---

## Configuration

```
AWS_REGION=ap-southeast-1
BEDROCK_GUARDRAIL_ID=<guardrail id>
BEDROCK_GUARDRAIL_VERSION=1
BEDROCK_GUARDRAILS_ENABLED=false
```

**Credentials are not configuration.** boto3 reads them from its own provider
chain — environment variables, shared config, or the IAM role attached to the
task in a deployment. Nothing in this application reads or holds an access key,
which is what lets the same code run locally now and under a role later.

Note that values in `backend/.env` are read by `Settings` but are *not* exported
to the process environment, so boto3 does not see credentials placed there.
Export them in the shell, or start the server with `uvicorn --env-file .env`.

### IAM

The runtime identity has **one action**: `bedrock:ApplyGuardrail`. It is scoped
to Laeria's own guardrail *and* to the APAC guardrail-profile resources for the
cross-region destinations that profile can route to — cross-region inference
means the call can land in more than one region, so a policy naming only the
home-region guardrail would fail. That is the accurate description: one action,
narrowly scoped, but not literally a single resource ARN.

Nothing wider. No `bedrock:InvokeModel`, no foundation-model access.

### Version pinning is enforced, not just advised

The version must be a pinned number. `DRAFT` is mutable, so a boundary pinned to
it could change in a console with no deploy. Setting `DRAFT` — or leaving the id
or version empty — does **not** quietly disable the guardrail: the service
treats it as an outage and refuses every protected request. See *Failure
policy*.

---

## The three boundaries

```
user instruction        ──► [guardrail: INPUT]  ──► planner / retrieval / LLM
Reddit + merchant text  ──► [guardrail: INPUT]  ──► LLM context
                                    │
                    the assembled prompt ──► [guardrail: INPUT] ──► the model
                                    │
model output            ──► [guardrail: OUTPUT] ──► user / action proposal
```

**Granular screening, then final verification.** Bedrock filters malicious
individual evidence — so one poisoned thread costs that thread and not the run —
and then verifies the exact assembled context before it enters the reasoning
model.

Both halves are needed, and neither substitutes for the other:

| | What it is for |
|---|---|
| **Granular** (per thread, per title, per candidate) | Dropping *one* bad item instead of the whole run; keeping the evidence counts honest; masking PII; explaining exclusions |
| **Assembled prompt** (the final string) | Catching what only exists in the *combination* — meaning that lives between two individually innocent fragments |

Pieces being individually safe does not make their combination safe. The
assembled prompt is therefore the last boundary, and the rule is absolute:

> **Every user message any model receives is a string Bedrock was asked about,
> byte for byte.**

Checking one string and then assembling a different one would leave the verdict
describing text that was never sent, so the guarded return value *is* what gets
sent. `test_the_checked_prompt_is_the_prompt_that_is_sent` asserts this across
research, shopping and monitoring.

External content is checked with `source=INPUT`, not `OUTPUT`, and that is
deliberate: Bedrock's `PROMPT_ATTACK` filter only runs on the input side, and
prompt injection hidden in someone else's comment or product title is the exact
threat this boundary exists to catch.

### 1. User input

* Research questions (Mode 2 and Mode 1), checked before the planner LLM, before
  Reddit is searched, and before the result cache is even read — a refused
  question must not be answerable by having been asked once before.
* `GET /research/subreddits`, which reaches the planning model directly.
* Shopping instructions, checked before the planning model and the storefront.

A refusal stops everything: no model call, no retrieval, no proposal, no
payment. The API answers **400** with a plain sentence.

### 2. External content

**Reddit.** Two passes, at two different granularities:

* *Titles*, before the relevance-screening model reads them. This is the first
  place external text meets a model and the easiest to overlook. The whole batch
  is checked as one string; only if that comes back refused does it re-check
  title by title to find which ones to drop. Normal runs cost one call.
* *Full threads*, before synthesis. Each thread is checked as exactly the
  untrusted block that would be rendered into the prompt (`_thread_content`
  builds both), so what was inspected is what would have been sent. A refused
  thread is excluded and the run continues on the rest — one poisoned thread
  costs that thread, not the whole research run.

**One sanitized copy per thread, reused everywhere.** The full-thread check
returns the masked text, and that single copy is what the synthesis prompt, the
embedding call and the duplicate warnings all use. Nothing re-derives its own
text from the raw thread afterwards, which is what stops masked data leaving
through a side door.

**Monitoring (Mode 3)** uses the same title screen before its classifying model,
and the same output check afterwards. Both halves of that prompt are guarded:
the posts, and the monitored item's own name.

### Every assembled prompt, and what it is made of

| Prompt | Assembled from | Final check |
|---|---|---|
| Subreddit planner | guarded query | yes |
| Relevance screen | guarded query + guarded title block | yes |
| Mode 2 synthesis | guarded query + sanitized corpus + machine warnings + thread headers | yes — once, reused by both halves |
| Mode 2 retry | guarded query + sanitized corpus | yes (a different string, so its own verdict) |
| Retrospective classifier | guarded decision + guarded title block | yes |
| Retrospective synthesis | guarded decision + sanitized corpus + warnings | yes |
| Monitor classifier | guarded item name + guarded post block | yes |
| Shopping planner | the guarded instruction, alone | no — the message *is* the string already checked, so a second call would be symmetry, not safety |
| Shopping chooser | guarded instruction + budget + planner notes + sanitized candidate lines | yes |

A refusal at this boundary fails closed in each flow's own idiom: an
`unsafe_evidence` brief, a quiet monitoring run, or no pick. Nothing guesses
which fragment caused a cross-source interaction, because no per-piece verdict
identified one.

### One invocation, or none

The final check sends the **entire** assembled prompt in a single
`ApplyGuardrail` request. A research prompt with a full corpus is around 50,000
characters and goes in whole.

**Nothing is ever split.** An earlier version divided long text into 20,000
-character pieces and checked each — which reintroduced, one level up, the exact
bug this boundary exists to prevent: two safe pieces do not make a safe whole,
and a cross-piece attack would land in separate requests and clear both.
Splitting is not a way to check a large prompt; it is a way to check something
else and call it the prompt.

Laeria therefore caps a single guarded string at **100,000 characters**. That
is an application ceiling, **not an AWS limit** — the standard-tier input
allowance is comfortably larger, and the cap sits well inside the smaller
documented text-unit classes while leaving generous room above the ~50k real
case. Anything larger is **refused**:

> "Safety verification could not evaluate the complete model context. Laeria did
> not continue."

No model call, no quota numbers shown to the user. Nothing in the application
approaches the ceiling: a user query is capped at 500 characters, a rendered
thread at roughly 8k, a title batch at roughly 8k, and the assembled prompt is
bounded by the thread budget.

The invariant this buys is worth stating exactly:

> **The exact complete string sent to OpenRouter was evaluated together by one
> Bedrock `ApplyGuardrail` invocation.**

We do not treat independently-safe fragments as proof that their combination is
safe.

### Where a boundary is enforced, and where it is merely convenient

The monitored item name is user-controlled text that ends up in the monitor
prompt. It is checked in **two** places, and only one of them is the boundary:

| Place | Role |
|---|---|
| `POST /monitor/items` | Early rejection. The user finds out immediately and gets a 400 instead of an item that fails silently later. |
| `AlertEngine.classify_run` | **The boundary.** Checked at the moment it would reach a model. |

The creation check cannot be the boundary, because plenty of items never pass
through it: rows written before this integration existed, seeded or imported
items, internal callers, and any future code constructing an `AlertEngine`
directly. The same reasoning applies to research — the route checks the query
for a fast, clear refusal, and the agent checks it again because the agent is
what every caller goes through.

A masked name is used in its sanitized form **in the prompt only**. The stored
item is never rewritten because the prompt used a cleaned copy of its name.

**Merchant.** Laeria does *not* send raw product-page text to any model, so
there is none to guard. What the browser reads from the results page is the set
of product handles; titles and prices come from the shop's product JSON. The
model is shown one rendered line per candidate:

```
1. handle=ski-wax | All-Temp Ski Wax | 24.95 | in stock
```

`price` and `available` are a number and a boolean this code formats itself.
**`handle` and `title` are merchant-controlled, and they are the entire
untrusted surface.** Those lines are guarded before the model sees them, and:

* a **refused** candidate is removed from the list, so the guarded agent cannot
  select it, propose it, card it or pay for it — and if the model names it
  anyway, the existing "unknown handle is refused" rule stops it a second time;
* a **masked** candidate stays, and the sanitized line is what the model is
  shown. The catalogue row is untouched, so the pick still resolves to the real
  handle, variant and price — the model's view being sanitized cannot change
  what is actually bought;
* a candidate whose **own handle** was masked is excluded, because the handle is
  how a choice is named and resolved, and an altered one could only ever name
  something unresolvable.

### 3. Model output

Model-authored **free text** goes through the OUTPUT guard before it can be
displayed or acted on — a research brief is not only read, it is what
`/research/act` spends money against.

Model-authored **structured values do not**, because they are constrained by
code instead, which is a stronger check than a content filter: a value that is
not one of a fixed set is replaced with a safe default, so there is nothing for
a guardrail to add.

| Flow | Free text through the OUTPUT guard | Structured values constrained by code |
|---|---|---|
| Research (Mode 2) | `consensus_pick`, `strengths`, `failure_modes`, `what_reviewers_miss`, `alternatives`, `red_flags`, `bias_notes` | `confidence` coerced to one of three words, then capped by the structural ceiling; every count computed from `UsableEvidence`, never from the model |
| Retrospectives (Mode 1) | `common_positives`, `common_regrets`, `surprising_findings`, `sample_bias` | `outcome_split` coerced to floats; `confidence` coerced, with the thin-coverage floor |
| Shopping | `reason`, and each rejection note | `handle` validated against the already-screened candidate list — a name that is not on it is refused; price, variant and URL come from the catalogue, never from the model |
| Monitoring | `summary`, `issue_tag` | `sentiment`, `signal_level`, `recommended_action` each validated against a fixed allow-list; `notable_thread_ids` resolved only against posts that passed the input guard |

Blocked strings are dropped, never rewritten. If the dropped string is the
consensus pick, the brief simply has no recommendation, and the existing rule
that a brief without a pick is LOW does the rest. For shopping, a blocked
reason means **no pick** — never a different product.

**Monitoring is the highest-stakes case.** An alert can carry a
`recommended_action`, and `monitor_worker` turns that into a pending action row
— model output there is one human approval away from money. So a refused
monitor summary produces a *quiet run*: signal level `none`, recommended action
`none`, and no alert. Not a partial alert with a missing summary.

---

## Failure policy

| State | Behaviour |
|---|---|
| Disabled | Clean no-op. No AWS call, no credentials needed, no coupling. |
| Enabled, AWS answers | Its verdict is final. |
| Enabled, AWS unreachable | **Fail closed.** HTTP 503, "Safety verification is temporarily unavailable. Laeria did not continue." |
| Enabled, configuration missing or invalid | **Fail closed**, identically. |

A safety check that did not run is not a safety check that passed. At a boundary
in front of money, the only defensible default is to stop.

**"Enabled but unusable" is an outage, never an opt-out.** An empty guardrail
id, an empty version, or a `DRAFT` version all refuse every protected request
rather than turning the boundary into a no-op. This distinction matters more
than it looks: an operator who sets `BEDROCK_GUARDRAILS_ENABLED=true` believes
the boundary is up, so reinterpreting their misconfiguration as "disabled" would
send every protected call straight through a wall they thought was there.
`enabled` therefore records intent only and is never downgraded by a config
problem; the reason is logged once at startup and exposed as `config_error` for
`scripts/check_guardrails`.

The one deliberate exception is retrieval breadth, not safety: if the *relevance*
screen (a separate, non-Bedrock LLM step) fails, the corpus is kept and the
confidence policy caps the verdict at MODERATE instead. Bedrock failures never
degrade like that.

Cross-region guardrail inference is enabled on the AWS side, which reduces the
chance of the unavailable path being taken at all.

---

## Masking

The guardrail masks `EMAIL`, `PHONE`, `NAME` and `ADDRESS`, and blocks
`PASSWORD`, `AWS_ACCESS_KEY`, `AWS_SECRET_KEY`, credential exfiltration, and
high-confidence prompt attacks and misconduct.

AWS reports masking and blocking with the *same* top-level action
(`GUARDRAIL_INTERVENED`). Only the per-policy `action` field distinguishes
"we removed an email address, carry on" (`ANONYMIZED`) from "we refuse this"
(`BLOCKED`), so the decision is read from the assessments, never from the
top-level value alone.

When content is masked, **the sanitized text is what continues** — into the
prompt, into the brief, onto the screen. The original is not restored. On a
block, `outputs` holds AWS's canned refusal message rather than the input, so it
is never used as content.

Anything unrecognised fails closed: an intervention this parser cannot explain
is treated as a block, and a "masked" verdict that arrives with no sanitized
text is refused rather than passed through raw.

### What is and is not masked

**Masked before it reaches any model or provider:**

* the user's question and the shopping instruction;
* Reddit thread titles going into the relevance screen and the monitor
  classifier;
* Reddit thread content going into synthesis;
* **the same content going to the embedding provider** — the duplicate detector
  reads the already-sanitized copy rather than the raw thread;
* the near-duplicate warnings appended to the synthesis prompt, which now carry
  a sanitized title and **no usernames at all**;
* merchant handles and titles going into the shopping prompt;
* everything the model writes back.

**Deliberately NOT masked:**

* **The source list shown to the user.** Titles there are the real ones,
  because each row links to the real Reddit thread — a masked title that did
  not match the page it points at would be its own kind of dishonesty.
* Scores, comment counts and dates — numbers this code formats itself.
* **Subreddit names are not in that category.** A slug is chosen by whoever
  created the community; Reddit only constrains its format. It carries no free
  prose, so it gets no per-field call of its own, but it is *not* our text and
  is not described as such: it reaches a model inside the assembled-prompt
  check, which is what covers it.

So the accurate claim is *"content Bedrock masks does not reach OpenRouter —
neither the completion endpoint nor the embedding endpoint"*, not *"all model
traffic is PII-masked"*: masking only removes what the guardrail's configured
policies detect, and with guardrails disabled nothing is masked at all.

---

## Observability

Every non-clean verdict is logged with the boundary name, the source
(INPUT/OUTPUT), the outcome, the AWS policy names, and the latency:

```
bedrock guardrail blocked at shopping instruction (source=INPUT):
    PROMPT_ATTACK, MISCONDUCT, TOPIC:Credential exfiltration [469ms]
```

**The inspected content is never logged, and neither is any part of it.** It may
hold credentials, private keys, personal data or an attacker's instructions.
AWS's `match` fields — which contain the offending substring itself — are
deliberately never read.

That rule covers identifiers carved out of the inspected text, which is subtler
than it sounds. A rejected item is named by something that is demonstrably not
attacker-authored:

| Rejected thing | Logged as | Why not the obvious label |
|---|---|---|
| Merchant candidate | its **position** in the list | the handle is merchant-written and is part of the refused line |
| Reddit thread | Reddit's **thread id** | assigned by Reddit; the subreddit name is community-chosen and appears in the refused title line |
| Monitored item | its **database id** | the name is user text the boundary may have just refused or masked |

Policy names and PII *types* (`PII:EMAIL`) are safe, and are what makes an
intervention explainable without reproducing what caused it.

Exclusions are also reported as data, not just logs, and each keeps its own
cause: `signal_quality.unsafe_threads_excluded` counts safety exclusions,
`off_topic_candidates_rejected` counts relevance exclusions, and
`guardrail_blocked_outputs` counts model strings refused on the way out.

### Well-Architected

* **Security** — least-privilege IAM: one action, `bedrock:ApplyGuardrail`,
  scoped to Laeria's guardrail and the required APAC cross-region
  guardrail-profile resources. An independent safety boundary in front of the
  model — granular screening plus verification of the exact assembled prompt —
  a deterministic spending mandate behind it, and no long-lived payment
  credential.
* **Reliability** — APAC cross-region guardrail; explicit, tested fail-closed
  behaviour rather than an implicit one, including for a context too large to
  verify as a unit. We do not treat independently-safe fragments as proof that
  their combination is safe.
* **Operational excellence** — interventions are visible and attributable
  without leaking the content that caused them.

---

## Verifying it for real

```
python -m scripts.check_guardrails
```

Two probes against the live guardrail: an ordinary shopping question that must
be ALLOWED, and an instruction-override plus credential-exfiltration attempt
that must be BLOCKED. A safe question being refused is as much a failure as an
attack getting through.

The automated test suite never touches AWS. `backend/tests/conftest.py` forces
`BEDROCK_GUARDRAILS_ENABLED=false` before any test runs, so "disabled is a clean
no-op" is a property the whole suite proves continuously; the guardrail tests
inject a fake bedrock-runtime client returning the response shapes AWS actually
returns.

---

## Claims we can make, and claims we cannot

**True, and tested:**

* Amazon Bedrock Guardrails sits at three trust boundaries around the agent:
  user input, external content entering the model, and model output leaving it.
* Prompt injection hidden in a Reddit thread or a merchant product title is
  detected and that item is excluded before any model reads it — one poisoned
  item costs that item, not the whole run.
* **Bedrock filters malicious individual evidence, and then verifies the exact
  assembled context before it enters the reasoning model.** An attack that only
  exists in the combination of two individually-innocent fragments is caught at
  that second check, and the run fails closed rather than guessing which
  fragment to blame.
* Every user message any model receives is a string Bedrock was asked about,
  byte for byte — the guarded return value is what gets sent.
* The exact complete string sent to OpenRouter was evaluated **together, by one
  `ApplyGuardrail` invocation**. Nothing is split into independently-trusted
  chunks; a prompt too large to evaluate as a unit is refused instead.
* A refused item cannot inflate the evidence counts, the represented
  communities, the confidence, or the displayed sources.
* A candidate rejected by Bedrock cannot be selected by the guarded
  `ShoppingAgent`, and cannot become an action through that agent path.
* Content the guardrail masks does not reach OpenRouter — not the completion
  endpoint and not the embedding endpoint.
* If the guardrail cannot be reached, the agent stops rather than continuing
  unverified.
* Guardrail verdicts can only ever *remove* evidence or output. Nothing in this
  layer can raise a confidence level or authorise a payment.
* With guardrails enabled, no user-controlled or external text reaches any
  model without passing Bedrock first. Every check sits at the point the text
  would enter a model, not at the point it happened to be typed, so data
  already in the database is covered too.

**Not true — do not say these:**

* ❌ *"Bedrock scans entire merchant web pages."* Laeria never sends raw page
  text to a model. The browser reads product **handles** from the results page;
  titles and prices come from the shop's product JSON. The untrusted surface is
  the handle and the title, and that is what is guarded.
* ❌ *"All model traffic is PII-masked."* Masking removes only what the
  configured policies detect, and only when guardrails are enabled.
* ❌ *"Bedrock validates our recommendations."* It judges content safety, not
  whether a recommendation is correct — that is the evidence engine.
* ❌ *"Bedrock authorises spending."* The mandate does that, unchanged.
* ❌ *"A malicious product can never be selected, proposed, carded or paid."*
  Too broad. The claim holds for the **guarded ShoppingAgent path**. The
  product also has a pre-existing Decision → Commerce handoff that reaches the
  storefront without going through that agent; it is outside this integration
  and was deliberately not changed here. Scope the claim to the agent path.
* ❌ *"Everything the model writes is checked by Bedrock."* Free text is;
  structured values are constrained by code instead. See the table above.

## Not done here, on purpose

* **Contextual grounding** is AWS Phase 2. The boundary comes first.
* **`/store/search`** returns catalogue rows straight to the browser without a
  guardrail. No model is involved on that path, so there is nothing to inject
  into; the guard is on the path where a model reads those fields.
