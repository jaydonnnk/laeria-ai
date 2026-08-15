# Amazon Bedrock Guardrails — the safety boundary around the agent

Laeria's thinking is done by OpenRouter and is unchanged by this work. Bedrock
is **not** an inference provider here. It answers exactly one question, at every
point where text crosses a trust boundary:

> Can this content safely enter or leave the agent?

That question is separate from everything else the system does, and it is
answered by a different vendor from the one doing the reasoning — which is the
point. A model cannot be the only thing deciding whether its own inputs are
trustworthy.

| Layer | Question it answers | What it does NOT do |
|---|---|---|
| **Bedrock Guardrails** | Can this content safely enter or leave the agent? | Judge whether an answer is *correct*, or authorise spending |
| Research / shopping / monitoring | Do the normal work | Read content for attacks |
| Mandate | May the agent spend this, here? | Read content at all |
| Payment layer | Execute only the authorised payment | Make decisions |

None of these replaces another. Bedrock decides what may be **considered**; the
mandate still decides what may be **spent**. A guardrail verdict has never
chosen a product or approved a payment, and the code has no path by which it
could.

Implementation: `backend/services/bedrock_guardrails.py`. Call sites:
`backend/agents/research_agent.py`, `shopping_agent.py`, `alert_engine.py`.
Tests: `backend/tests/test_bedrock_guardrails.py`. Live check:
`backend/scripts/check_guardrails.py`.

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

The runtime identity has **one action**: `bedrock:ApplyGuardrail`, scoped to
Laeria's guardrail and the required APAC cross-region guardrail-profile
resources. Cross-region inference means the call can land in more than one
region, so a policy naming only the home-region guardrail would fail — that is
the accurate description: one action, narrowly scoped, but not literally a
single resource ARN.

No `bedrock:InvokeModel`. No foundation-model access.

### Version pinning is enforced, not just advised

The version must be a pinned number. `DRAFT` is mutable, so a boundary pinned to
it could change in a console with no deploy. Setting `DRAFT` — or leaving the id
or version empty — does **not** quietly disable the guardrail: the service
treats it as an outage and refuses every protected request. See *Failure
policy*.

---

## The boundaries

```
user instruction        ──► [guardrail: INPUT]  ──► planner / retrieval / LLM
Reddit + merchant text  ──► [guardrail: INPUT]  ──► LLM context
                                    │
                    the assembled prompt ──► [guardrail: INPUT] ──► the model
                                    │
model output            ──► [guardrail: OUTPUT] ──► user / alert / proposal
```

External content is checked with `source=INPUT`, not `OUTPUT`, and that is
deliberate: Bedrock's `PROMPT_ATTACK` filter only runs on the input side, and
prompt injection hidden in someone else's comment or product title is the exact
threat this boundary exists to catch.

**Granular screening, then final verification.** Bedrock filters malicious
individual evidence — so one poisoned thread costs that thread and not the run —
and then verifies the exact assembled context before it enters the model.

| | What it is for |
|---|---|
| **Granular** (per thread, per title, per candidate) | Dropping *one* bad item instead of the whole run; masking PII; explaining exclusions |
| **Assembled prompt** (the final string) | Catching what only exists in the *combination* — meaning that lives between two individually-innocent fragments |

Pieces being individually safe does not make their combination safe. The rule is
therefore absolute:

> **Every user message any model receives is a string Bedrock was asked about,
> byte for byte** — and the guarded return value is what gets sent.

`test_the_checked_prompt_is_the_prompt_that_is_sent` asserts this across
research, shopping and monitoring.

### Two kinds of model call, two different guarantees

OpenRouter is reached in two ways, and they are not the same shape. Saying so
precisely matters, because an embedding call is not an assembled prompt:

| Call | Guarantee |
|---|---|
| **Completion / classification** | The exact complete string gets one final `ApplyGuardrail` verdict immediately before it is sent. |
| **Embedding** (duplicate detection) | Each external thread's model-view is *already* Bedrock-sanitized before it is embedded. There is no assembled prompt here to verify — the input is one thread's text, and that text is the guardrail's own output. |

The embedding call is the one that is easy to miss: it happens **before** the
synthesis prompt is assembled and masked, so it is a second exit from the agent.
The full-thread check keeps its sanitized result and hands it to the duplicate
detector, so no consumer re-derives an unmasked copy from the raw thread. The
formula is unchanged — title plus the first 600 characters of the body — only
its source is the cleaned copy. With guardrails disabled the raw thread is used
and behaviour is exactly current master's.

### One invocation, or none

The final check sends the **entire** assembled prompt in a single
`ApplyGuardrail` request. A research prompt with a full corpus is around 50,000
characters and goes in whole.

**Nothing is ever split.** Dividing long text into pieces and checking each
would reintroduce, one level up, the exact bug this boundary prevents: two safe
pieces do not make a safe whole, and a cross-piece attack would land in separate
requests and clear both. Splitting is not a way to check a large prompt; it is a
way to check something else and call it the prompt.

Laeria therefore caps a single guarded string at **100,000 characters**. That is
an application ceiling, **not an AWS limit** — the standard-tier input allowance
is comfortably larger. Anything larger is **refused**:

> "Safety verification could not evaluate the complete model context. Laeria did
> not continue."

No model call, and no quota numbers shown to the user. Nothing in the
application approaches it: a user query is capped at 500 characters, a rendered
thread at roughly 8k, a title batch at roughly 8k.

### Research

* The user's question, before the planner, before Reddit is searched, and before
  the result cache is read — a refused question must not be answerable by having
  been asked once already.
* Each retrieved thread, checked as exactly the text `_build_corpus` would
  render for it. A refused thread is excluded and the run continues on the
  rest; if nothing survives, the existing empty-brief path is used.
* Candidate titles going into the retrospective classifier.
* The assembled synthesis prompt — checked once and reused by both halves, which
  send the identical user message.
* The model's free text on the way out.

This PR protects content. It does **not** judge evidence quality: there is no
confidence ceiling, no evidence-set architecture and no new evidence state in
it. If too little content survives, the normal no-result behaviour applies.

**Cached briefs are screened too.** A cache hit skips synthesis, and would
otherwise skip the output guardrail with it — the research cache is disk-backed
and survives restarts on purpose, so an entry written before this boundary
existed, or while it was switched off, can be served long afterwards. When
guardrails are enabled, a brief coming back off disk has its model-authored free
text checked before it is returned: the pick, the strengths, the failure modes,
what reviewers miss, the alternatives, the red flags, and `bias_notes` — which
sits nested under `signal_quality` once a brief is stored, a different shape
from the live path. Counts, dates, the subreddit list, source ids and the
confidence word are not free text and are left alone. An outage while screening
a cached brief fails closed.

### Shopping

Laeria does **not** send raw product-page text to any model, so there is none to
guard. What the browser reads from the results page is the set of product
handles; titles and prices come from the shop's product JSON. The model is shown
one rendered line per candidate:

```
1. handle=ski-wax | All-Temp Ski Wax | 24.95 | in stock
```

`price` and `available` are a number and a boolean this code formats itself.
**`handle` and `title` are merchant-controlled, and they are the entire
untrusted surface.** Those lines are guarded before the model sees them, and:

* a **refused** candidate is removed, so the guarded agent cannot select it,
  propose it, card it or pay for it — and if the model names it anyway, the
  pre-existing "unknown handle is refused" rule stops it a second time;
* a **masked** candidate stays, and the sanitized line is what the model sees.
  The catalogue row is untouched, so the pick still resolves to the real handle,
  variant and price;
* a candidate whose **own handle** was masked is excluded, because the handle is
  how a choice is named and resolved.

The complete chooser prompt then gets its own check. A refusal means **no
pick** — never a different product, and never a fallback to first-in-stock.

### Monitoring

The monitored item's name and the retrieved post titles are checked before they
enter the prompt, the assembled classifier prompt is checked as one string, and
the model's summary is checked before it can become findings.

That last one matters most here: an alert can carry a `recommended_action`, and
the worker turns that into a pending action row — model output on this path is
one human approval away from money. So a refused summary produces a **quiet
run**: signal level `none`, recommended action `none`, no alert. Not a partial
alert with a missing summary.

The pure `evaluate()` comparison logic is untouched — no network, no model, no
guardrail in it.

---

## Failure policy

| State | Behaviour |
|---|---|
| Disabled | Clean no-op. No AWS call, no credentials needed, no coupling. |
| Enabled, AWS answers | Its verdict is final. |
| Enabled, AWS unreachable | **Fail closed.** HTTP 503, "Safety verification is temporarily unavailable. Laeria did not continue." |
| Enabled, configuration missing or invalid | **Fail closed**, identically. |
| Enabled, prompt above the ceiling | **Fail closed**, with the context-size message. |

A safety check that did not run is not a safety check that passed. At a boundary
in front of money, the only defensible default is to stop.

**"Enabled but unusable" is an outage, never an opt-out.** An empty guardrail id,
an empty version, or a `DRAFT` version all refuse every protected request rather
than turning the boundary into a no-op. An operator who sets
`BEDROCK_GUARDRAILS_ENABLED=true` believes the boundary is up, so reinterpreting
their misconfiguration as "disabled" would send every protected call straight
through a wall they thought was there. `enabled` records intent only; the reason
is logged once at startup and exposed as `config_error`.

Refusals reach the API as **400** (refused — retrying changes nothing) and
outages as **503** (try again). Neither carries a stack trace or an AWS message.

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
prompt, onto the screen. The original is not restored. On a block, `outputs`
holds AWS's canned refusal message rather than the input, so it is never used as
content.

Anything unrecognised fails closed: an intervention this parser cannot explain
is treated as a block, and a "masked" verdict that arrives with no sanitized
text is refused rather than passed through raw.

Deliberately **not** masked: the source list shown to the user keeps real thread
titles, because each row links to the real Reddit thread and a masked title that
did not match the page it points at would be its own kind of dishonesty.

---

## Observability

Every non-clean verdict is logged with the boundary name, the source, the
outcome, the AWS policy names, and the latency:

```
bedrock guardrail blocked at shopping instruction (source=INPUT):
    PROMPT_ATTACK, MISCONDUCT, TOPIC:Credential exfiltration [469ms]
```

**The inspected content is never logged, and neither is any part of it.** AWS's
`match` fields — which contain the offending substring itself — are deliberately
never read. A rejected item is named by something that is demonstrably not
attacker-authored:

| Rejected thing | Logged as | Why not the obvious label |
|---|---|---|
| Merchant candidate | its **position** in the list | the handle is merchant-written and part of the refused line |
| Reddit thread | Reddit's **thread id** | assigned by Reddit, not by the author |
| Monitored item | its **database id** | the name is user text the boundary may have just refused |

The monitor worker holds the raw database name even after the runtime boundary
has refused or masked it, so **its** logs are a separate exit and are covered
separately: the no-subreddit warning, the alert line, the checked line, the
proposed-action line and the per-item failure line all name the item by id.
Severity, signal level, action type and post count stay, so the logs are still
useful.

This is about logs specifically. The original name legitimately remains in the
database, in the monitor UI, in the action description and in the user-facing
Obsidian note — those are the user's own data, shown back to them, not
operational logging.

The same distinction applies to the research cache. It is looked up by the words
the user actually typed, because that is the right identity for a cache — but
the cache-hit log line names the entry by its own **hash filename** instead. If
the safety layer masked an address out of a question, reproducing that question
in a log would put it straight back.

### Well-Architected

* **Security** — least-privilege IAM: one action, `bedrock:ApplyGuardrail`,
  scoped to Laeria's guardrail and the required APAC cross-region
  guardrail-profile resources. An independent safety boundary in front of the
  model, the deterministic spending mandate behind it, and no long-lived payment
  credential.
* **Reliability** — APAC cross-region guardrail; explicit, tested fail-closed
  behaviour, including for a context too large to verify as a unit. We do not
  treat independently-safe fragments as proof that their combination is safe.
* **Operational excellence** — interventions are visible and attributable
  without leaking the content that caused them.

---

## Verifying it for real

```
python -m scripts.check_guardrails
```

Two probes against the live guardrail: an ordinary shopping question that must be
ALLOWED, and an instruction-override plus credential-exfiltration attempt that
must be BLOCKED. A safe question being refused is as much a failure as an attack
getting through.

The automated test suite never touches AWS. `backend/tests/conftest.py` forces
`BEDROCK_GUARDRAILS_ENABLED=false` before any test runs, so "disabled is a clean
no-op" is a property the whole suite proves continuously; the guardrail tests
inject a fake bedrock-runtime client returning the response shapes AWS actually
returns.

---

## Claims we can make, and claims we cannot

**True, and tested:**

* Bedrock Guardrails sits at the trust boundaries around the agent: user input,
  external content entering the model, the assembled prompt, and model output.
* Prompt injection hidden in a Reddit thread or a merchant product title is
  detected and that item is excluded before any model reads it — one poisoned
  item costs that item, not the whole run.
* Bedrock filters malicious individual evidence, then verifies the exact
  assembled context before it enters the model. The complete string sent to an
  OpenRouter *completion* was evaluated together, by one `ApplyGuardrail`
  invocation.
* Reddit content rejected by Bedrock reaches **neither** an OpenRouter
  completion **nor** an embedding call.
* Reddit PII masked by Bedrock reaches OpenRouter only in sanitized form —
  **including the embedding call**, which happens before the prompt is
  assembled and is therefore checked separately.
* Operational logs do not reproduce refused or masked monitored-item text, or
  the raw research query on a cache hit.
* Model-authored free text is checked on its way out **including from the
  cache** — legacy entries written while the boundary was disabled are screened
  before they are returned.
* A candidate rejected by Bedrock cannot be selected by the guarded
  `ShoppingAgent`, and cannot become an action through that agent path.
* If the guardrail cannot be reached, or cannot evaluate the context as a unit,
  the agent stops rather than continuing unverified.

**Not true — do not say these:**

* ❌ *"Bedrock scans entire merchant web pages."* Laeria never sends raw page
  text to a model. The untrusted surface is the handle and the title.
* ❌ *"All model traffic is PII-masked."* Masking removes only what the
  configured policies detect, and only when guardrails are enabled.
* ❌ *"Bedrock validates our recommendations."* It judges content safety, not
  whether an answer is correct.
* ❌ *"Bedrock authorises spending."* The mandate does that, unchanged.
* ❌ *"Bedrock replaces OpenRouter."* OpenRouter still does all the reasoning.
* ❌ *"A malicious product can never be selected, proposed, carded or paid."*
  Too broad. The claim holds for the **guarded `ShoppingAgent` path**. The
  product also has a pre-existing Decision → Commerce handoff that reaches the
  storefront without going through that agent; it is outside this integration
  and was deliberately not changed here.

---

## Not done here, on purpose

* **Contextual grounding** is a later phase. The boundary comes first.
* **`/store/search`** returns catalogue rows straight to the browser without a
  guardrail. No model is involved on that path, so there is nothing to inject
  into; the guard is on the path where a model reads those fields.
* **Evidence quality.** This branch protects content. Judging whether evidence
  supports a recommendation is separate work and is not part of this PR.
