# Signal Filters

Reddit signal is not uniformly trustworthy. These filters must be applied
before weighting any post, in all three modes. Build them in Phase 1 — not
as a later hardening step.

The sections below describe what the synthesis prompt asks the model to do.
That is a real part of the design — reading sarcasm and judging whether people
actually agree are semantic questions only a model can answer — but an
instruction is not an invariant. Where a rule can be checked structurally, it
is now checked in code as well; see **Deterministic confidence ceilings**
below for the list, and treat everything above that section as prompt-level
unless it says otherwise.

## Account age filter
Ignore posts from accounts under 6 months old when they make product
recommendations or warnings. New accounts are disproportionately used for
shill campaigns and astroturfing.

## Upvote validation
A top-level comment with 500+ upvotes standing for 6+ months carries far more
weight than a recent post with 10 upvotes. Weight by validated longevity.

## Cross-subreddit corroboration
If a claim appears in only one subreddit, weight it lower. The same signal
appearing independently across 3+ subreddits is high-confidence.

## Recency vs longevity
For product *quality* issues, recent posts (last 3 months) matter more —
products change. For fundamental consensus ("best budget X"), older validated
threads outweigh recent single posts.

## WARP poisoning awareness
As of the May 2026 Cornell Tech paper, Reddit is being actively poisoned by
coordinated campaigns *specifically because* AI agents trust it. A single
highly-upvoted post recommending an obscure vendor, or text that reads
unusually polished for the context, warrants skepticism. Corroborate across
multiple posts and multiple accounts before acting on any recommendation.

## Relevance of retrieved threads
Reddit search is literal keyword matching, so a search returns threads that
share a word with the query and nothing else — a keyboard question in
Singapore retrieves "where to go to buy laptops in SG" and "best quiet area in
SG for long term stay". These threads are often popular, so an engagement
filter cannot remove them.

**Enforced in code.** `ResearchAgent._screen_relevance` runs one batched LLM
call over candidate TITLES before any thread is fetched, and keeps only those
that could bear on the question. It is deliberately generous — an ambiguous
title is kept — because dropping a relevant thread costs more than reading a
doubtful one.

It fails open and says so: an LLM outage keeps every candidate and records
`relevance_screened: false`, which rule 8 below then acts on. An explicit
"none of these are relevant" verdict is different from a failure and is
honoured — the run ends as `no_evidence`.

## Sarcasm and context
Reddit uses heavy sarcasm. Read comment chains, not just top-level posts.
"Oh yeah, X is great — if you like wasting money" is not positive signal.

---

## Deterministic confidence ceilings

Implemented in `backend/agents/confidence.py`, applied to Mode 2 in
`ResearchAgent._synthesise`, covered by `backend/tests/test_confidence_policy.py`.

The model still judges whether people agree — that is the semantic question,
and it produces `semantic_confidence`. Separately, the rules below look only at
the SHAPE of the evidence and produce a `structural_ceiling`. The verdict is
the more conservative of the two:

```
final = min(semantic_confidence, structural_ceiling)
```

**These rules can only ever lower a verdict.** A clean evidence shape is not
evidence that the threads agree, so code never promotes a cautious model to
HIGH; only the model can raise confidence, and only structure can cap it.

No percentages, weights, or scores are produced anywhere in this policy. The
three bands are the whole vocabulary.

| # | Condition | Ceiling | Where the threshold comes from |
|---|-----------|---------|-------------------------------|
| 1 | No usable threads | LOW | Nothing to be confident about |
| 2 | Exactly one usable thread | LOW | One thread cannot corroborate itself |
| 3 | Evidence spans fewer than 3 represented communities | MODERATE | The cross-subreddit rule above: "3+ subreddits is high-confidence". Measures spread, **not** independence — see below |
| 4 | Signal filters had to relax | MODERATE | Reuses the existing 5-strong-thread bar in `apply_signal_filters` — no new number, and the number is not restated in the policy module |
| 5 | Cross-author near-duplicates detected | MODERATE | The WARP section above |
| 6 | Similarity analysis could not run | MODERATE | A check that did not execute cannot back a claim |
| 7 | No consensus pick (final, not a ceiling) | LOW | "High confidence in no recommendation" is incoherent |
| 8 | Retrieved threads could not be screened for relevance | MODERATE | A check that did not execute cannot back a claim — the same reasoning as rule 6 |

Rule 3 counts communities **represented in the final corpus**, not communities
the planner searched. A subreddit that was read and yielded nothing did not
corroborate anything — and neither did one that only contributed threads about
a different topic, which is what the relevance screen above removes. Before
that screen existed, four popular off-topic threads from two Singapore
subreddits could turn a two-community keyboard corpus into a four-community
one, and rule 3 would then permit HIGH.

**Rule 3 measures spread, not independence.** What it counts is distinct
subreddit names, and names cannot prove that those communities reached their
view separately — a single claim can be repeated across several subreddits
from one upstream source, and no count can see that. The rule is therefore
worded as *"evidence spanning at least 3 communities"* rather than *"3
independent sources"*, and the code makes no claim to have verified
independence. Judging whether the substance actually agrees remains the
model's job; this rule only refuses to call a narrow spread "high".

**Rule 5 is a conservative safety policy, not an accusation.** Near-identical
text under different accounts has innocent explanations — quoting, reposting, a
shared source article. The detector does not prove coordination and this
project does not claim it does; nor does it establish anything about whether
sources are independent, which this system does not attempt to verify.

What it does establish is narrower, and enough: those items cannot safely be
counted as *separate supporting evidence*. Because HIGH is intended to require
stronger corroboration than the other bands, near-duplicate content under
different authors is sufficient reason to withhold it. Alleging manipulation
would not be.

**Rule 6 caps rather than floors deliberately.** An embeddings outage says
nothing about the threads themselves, so downgrading real evidence to LOW over
an infrastructure failure would be its own dishonesty. But one of the
structural anti-manipulation checks behind a HIGH claim did not run, so HIGH
cannot be claimed.

Each rule that fires contributes one plain-language reason to
`ResearchBrief.confidence_reasons`. Rules that do not fire contribute nothing,
so the list never contains a clean bill of health — every line names a real
limitation of that specific corpus.

A brief with no corpus at all carries a reason derived from its
`evidence_state` instead, because the ceiling rules can only observe that the
thread count is zero and cannot tell a blocked source from an empty search.

The frontend renders policy reasons verbatim. It separately derives a short
relationship headline from the typed `semantic_confidence`,
`structural_ceiling`, `confidence` and `consensus_pick` fields; it never
infers a structural cause from prose.

---

## One authoritative evidence set

`agents/evidence.py` holds `UsableEvidence`: the threads a brief was actually
synthesised from, after relevance screening, retrieval, signal filtering and
duplicate collapsing. Every displayed fact about the shape of the evidence is
measured over that one object — the thread count, the represented communities,
the strong-thread count, the confidence stats and the source list. A thread
that was searched for, rejected as off-topic, failed to fetch, or was collapsed
as a duplicate is in none of them.

**Structural claims belong to the code, not the model.** The synthesis prompt
now forbids the model from writing about how many threads or communities there
are, and any claim it writes anyway is checked against `UsableEvidence` before
display. One contradiction is recognised: asserting that the evidence comes
from a single community when it demonstrably spans two or more. That claim is
removed and counted in `signal_quality.unverified_claims_removed`.

This is the fix for a brief that displayed "8 threads across r/lasik,
r/eyetriage, r/optometry, r/vision" above a red flag reading "all evidence from
a single subreddit (r/lasik) — no cross-community corroboration".

The check is narrow on purpose:

* When the corpus really *is* one community, the same sentence is true and is
  kept. This is a consistency check, not a way to hide bad news.
* It only removes what it can prove false. Numeric claims, staleness claims
  and everything about what people *said* are untouched — silently editing the
  model's judgement would be a worse failure than the one being fixed.
* The truthful version of a narrow-corpus warning is not lost: rule 3 emits
  "evidence is represented in N communities" deterministically, as a
  confidence reason.

**Not yet enforced in code**, and still prompt-level only: staleness/recency
weighting, contradiction between communities, account age (not obtainable from
HTML scraping), and sarcasm. Mode 1 (retrospectives) keeps its own separate
deterministic floor — `thin_coverage` forces LOW below 5 retrospectives — and
is not covered by the ceiling policy above.

---

Note on the Cornell paper: this reference is written from the project's design
notes. Before citing the paper externally, verify the exact title, authors,
and publication venue directly — do not treat this summary as an authoritative
citation.
