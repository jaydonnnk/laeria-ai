# Signal Filters

Reddit signal is not uniformly trustworthy. These filters must be applied
before weighting any post, in all three modes. Build them in Phase 1 — not
as a later hardening step.

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

## Sarcasm and context
Reddit uses heavy sarcasm. Read comment chains, not just top-level posts.
"Oh yeah, X is great — if you like wasting money" is not positive signal.

---

Note on the Cornell paper: this reference is written from the project's design
notes. Before citing the paper externally, verify the exact title, authors,
and publication venue directly — do not treat this summary as an authoritative
citation.
