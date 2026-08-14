"""The authoritative account of what evidence a brief is actually built on.

One brief, one evidence set. Every displayed fact about the SHAPE of the
evidence — how many threads were read, which communities they came from, how
many of them cleared the quality bar — is derived from this object, so the
verdict card, the confidence policy and the source list cannot disagree about
the same brief.

Before this existed the counts were derived correctly but the *prose* was not:
`red_flags` and `bias_notes` arrive from the model as free text and were passed
straight through to the screen. A brief could therefore say "8 threads across
r/lasik, r/eyetriage, r/optometry, r/vision" in the verdict card and "all
evidence from a single subreddit (r/lasik)" in the red flags, both at once. The
model had never been given a way to be wrong about that, and nothing checked.

So structural claims are OWNED here and by `agents.confidence`, not by the
model:

* the counts come from `UsableEvidence`;
* the honest structural limitations come from the confidence policy, which
  emits "evidence is represented in N communities" deterministically;
* a model-authored claim that CONTRADICTS the evidence set is removed before
  display, and the count of removals is reported rather than hidden.

The contradiction check is deliberately narrow. It only drops a claim it can
prove false against this corpus — a single-community assertion over evidence
that demonstrably spans several. Anything it cannot definitively contradict is
kept, because silently editing the model's judgement about what people SAID
would be a different and worse failure than the one being fixed. It is a
consistency check, not a censor.

Pure: no network, no LLM, no clock, no database.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from core.logging import get_logger
from core.models import RedditThread

logger = get_logger(__name__)


@dataclass(frozen=True)
class UsableEvidence:
    """The threads a brief was actually synthesised from — nothing else.

    "Usable" means the thread survived every stage that can reject one:
    relevance screening, retrieval, the signal filters, and duplicate
    collapsing. A thread that was searched for, retrieved but rejected, or
    dropped as a near-duplicate is not here, and therefore cannot inflate any
    number the user is shown.
    """

    threads: tuple[RedditThread, ...] = ()

    @classmethod
    def of(cls, threads: Sequence[RedditThread]) -> UsableEvidence:
        return cls(tuple(threads))

    @property
    def thread_count(self) -> int:
        return len(self.threads)

    @property
    def represented_subreddits(self) -> tuple[str, ...]:
        """Distinct communities present in this corpus, deterministically ordered.

        Sorted rather than first-seen so the same corpus always renders the
        same list, and empty subreddit names are excluded — an unparsed name is
        not a community.
        """
        return tuple(sorted({t.subreddit for t in self.threads if t.subreddit}))

    @property
    def community_count(self) -> int:
        return len(self.represented_subreddits)

    def strong_thread_count(self, strong_ids: Iterable[str]) -> int:
        """How many threads IN THIS CORPUS cleared the engagement quality bar.

        Takes the ids of the threads that cleared it rather than re-applying
        the thresholds: the bar belongs to `RedditService.apply_signal_filters`
        and restating the numbers here would let the two drift.

        Counted over the final corpus, not over everything fetched, so the
        "only N threads cleared the bar" line can never quote a number larger
        than the thread count printed beside it.
        """
        ids = set(strong_ids)
        return sum(1 for t in self.threads if t.id in ids)


# ---- structural-claim verification ---------------------------------------
#
# These patterns match one family of claim only: "this evidence all comes from
# a single community". That is the family the model demonstrably gets wrong,
# it is cheap to check, and the truthful version of it is already emitted
# deterministically by the confidence policy (rule 3). Numeric claims, staleness
# claims and everything else are NOT checked here — an unverifiable claim is
# left alone rather than guessed at.

# Words that scope a sentence to the WHOLE corpus. "the evidence from r/lasik
# is thin" is a comment about part of it and is left alone; "all the evidence
# is from r/lasik" is a claim about every thread, and that is checkable.
_WHOLE_CORPUS = (
    r"(all|everything|every\s+(thread|post|source|discussion|comment)|"
    r"the\s+(entire|whole))"
)

_SINGLE_COMMUNITY = (
    # "a single subreddit", "only one community", "just one sub"
    re.compile(
        r"\b(single|sole|solitary|only one|just one)\s+(sub|subreddit|community)\b",
        re.I,
    ),
    # hyphenated adjective forms: "single-subreddit sample", "one-community"
    re.compile(r"\b(single|one)[-‑](sub|subreddit|community)\b", re.I),
    # "all of the evidence comes from one subreddit / from the same community"
    re.compile(
        rf"\b{_WHOLE_CORPUS}\b[^.;]{{0,60}}\bfrom\s+(a|one|the\s+same)\s+"
        r"(single\s+)?(sub|subreddit|community)\b",
        re.I,
    ),
    # "all evidence is from r/lasik" — a named community standing for the whole
    # corpus.
    re.compile(rf"\b{_WHOLE_CORPUS}\b[^.;]{{0,60}}\bfrom\s+r/\w+", re.I),
    # "no cross-community corroboration", "lacks cross-subreddit support"
    re.compile(
        r"\b(no|not|non|lack\w*|without|zero|absent|missing)\b[^.;]{0,40}"
        r"cross[-\s](communit|sub)",
        re.I,
    ),
)

# A claim that also says the evidence spans SEVERAL communities is not the
# claim being checked for — it is either self-contradictory prose or a comment
# about a subset ("more than one subreddit"), and neither is something this
# check should act on. When in doubt, keep the claim.
_MENTIONS_MULTIPLE = re.compile(
    r"\b(more than one|across (multiple|several|both|\d+)|multiple (sub|communit)|"
    r"several (sub|communit)|both (sub|communit)|(two|three|four|\d+) (sub|communit))",
    re.I,
)


def _asserts_single_community(claim: str) -> bool:
    if _MENTIONS_MULTIPLE.search(claim):
        return False
    return any(p.search(claim) for p in _SINGLE_COMMUNITY)


def contradicts(claim: str, evidence: UsableEvidence) -> bool:
    """Does this claim state something the evidence set proves false?

    Exactly one contradiction is recognised: asserting the evidence comes from
    a single community when it demonstrably spans two or more. With one
    community represented the same sentence is TRUE, and is kept — the point is
    consistency with the corpus, not suppressing bad news.
    """
    return evidence.community_count >= 2 and _asserts_single_community(claim)


def verified_claims(
    claims: Sequence[str], evidence: UsableEvidence
) -> tuple[list[str], int]:
    """Model-authored claims minus any the evidence set contradicts.

    Returns the surviving claims and how many were removed, so the removal is
    reportable rather than invisible.
    """
    # A model that answers with a bare string where the schema asks for a list
    # would otherwise be iterated character by character. One string is one
    # claim.
    if isinstance(claims, str):
        claims = [claims]
    kept: list[str] = []
    removed = 0
    for claim in claims:
        text = str(claim)
        if contradicts(text, evidence):
            removed += 1
            logger.warning(
                "dropped a structural claim contradicted by the evidence set "
                "(%d communities): %r",
                evidence.community_count,
                text[:120],
            )
            continue
        kept.append(text)
    return kept, removed


def verified_prose(text: str, evidence: UsableEvidence) -> tuple[str, int]:
    """The same check applied to a paragraph, sentence by sentence.

    `bias_notes` is one block of prose, and discarding the whole paragraph over
    a single wrong sentence would throw away honest commentary about sample
    bias. Only the contradicted sentences are removed.
    """
    if not text.strip():
        return text, 0
    sentences = re.split(r"(?<=[.!?])\s+", text.strip())
    kept, removed = verified_claims(sentences, evidence)
    return " ".join(kept).strip(), removed
