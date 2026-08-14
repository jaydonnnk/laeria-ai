"""Deterministic structural ceilings on Mode 2 research confidence.

The model reads the threads and judges whether people agree — that is a
semantic question and it stays with the LLM. This module answers a different
and entirely structural question: *given the shape of the evidence, how much
confidence is that shape allowed to earn?*

The two are combined conservatively::

    final = min(semantic_confidence, structural_ceiling)

so the ceiling can only ever LOWER a verdict. Code never promotes a cautious
model to HIGH — a structural rule saying "nothing here forbids HIGH" is not
evidence that the threads actually agree, and only the model can answer that.

Everything here is pure: no network, no LLM, no database, no clock, no
filesystem. That is deliberate — it makes the entire confidence policy
exhaustively testable without credentials or fixtures, which is what lets the
rules be regression-tested rather than merely asserted in a prompt.

Before this module existed, the structural findings in `signal_analysis`
(single-subreddit coverage, cross-author near-duplicates) were rendered into
English, concatenated into the synthesis prompt, and discarded — the pipeline
asked the model to "cap confidence accordingly" and had no way to insist. The
rules below are that same intent, enforced.

Phase A implements seven ceiling rules plus one final-verdict rule. Staleness,
contradiction detection and geography handling are deliberately NOT here; see
docs/SIGNAL_FILTERS.md for what is enforced in code versus what remains a
prompt-level instruction.
"""

from __future__ import annotations

from dataclasses import dataclass

from core.models import ConfidenceLevel, EvidenceState

# Explicit ordering, because `ConfidenceLevel` is a `str` Enum and comparing
# members directly compares their VALUES: "high" < "low" < "moderate"
# alphabetically, which ranks HIGH as the weakest of the three. Every
# comparison in this module goes through this map; none uses `<` on the enum.
_ORDER: dict[ConfidenceLevel, int] = {
    ConfidenceLevel.LOW: 0,
    ConfidenceLevel.MODERATE: 1,
    ConfidenceLevel.HIGH: 2,
}

# HIGH requires evidence spanning at least this many distinct communities.
# Not an invented threshold: docs/SIGNAL_FILTERS.md makes 3+ subreddits the
# project's own definition of the top confidence band.
#
# What this counts is distinct subreddit NAMES, which is a proxy for
# independence and not a proof of it — the same claim can be repeated across
# several communities from one upstream source, and no count can see that.
# Judging whether the substance actually agrees stays with the model; this
# rule only refuses to call a narrow spread "high".
MIN_SUBREDDITS_FOR_HIGH = 3

# NOTE: the "5 strong threads" bar deliberately does NOT appear in this module.
# It belongs to `RedditService.apply_signal_filters`, which owns the decision
# and reports it as `filters_relaxed`. Mirroring the number here to build an
# explanation string would let the two drift the moment one is tuned, and the
# explanation would then describe a threshold that is no longer in force.


@dataclass(frozen=True)
class EvidenceStats:
    """The structural shape of the corpus a brief was synthesised from.

    Every field is an observation the pipeline already made; nothing here is
    inferred or estimated. Defaults are the fail-closed reading of "we do not
    know", so a partially-populated instance yields a conservative ceiling
    rather than an optimistic one.
    """

    usable_thread_count: int = 0
    strong_thread_count: int = 0
    # Distinct subreddits present in the FINAL corpus handed to synthesis —
    # what actually contributed evidence, not what the planner hoped to read.
    represented_subreddits: tuple[str, ...] = ()
    filters_relaxed: bool = False
    cross_author_duplicate_count: int = 0
    # False means the duplicate detector could not produce a trustworthy
    # verdict for this corpus (embeddings failed), NOT that duplicates were
    # found. See RULE 6.
    similarity_analysis_available: bool = False
    # False means retrieved threads were never screened for relevance to the
    # question, NOT that everything retrieved was found relevant. See RULE 8.
    relevance_screened: bool = False
    has_consensus_pick: bool = False


@dataclass(frozen=True)
class ConfidenceOutcome:
    """What the model proposed, what the evidence permits, and the verdict."""

    semantic: ConfidenceLevel
    ceiling: ConfidenceLevel
    final: ConfidenceLevel
    reasons: tuple[str, ...]


def structural_ceiling(stats: EvidenceStats) -> tuple[ConfidenceLevel, list[str]]:
    """The highest confidence this evidence SHAPE can justify, and why.

    Returns the ceiling plus one explanation per rule that actually fired.
    Rules that did not fire contribute nothing, so the reason list is never
    padded with clean bills of health — a caller can render it verbatim and
    every line will name a real limitation of this specific corpus.

    The ceiling is the most conservative of all fired rules. Several rules
    firing at once cannot produce anything lower than the strictest of them.
    """
    reasons: list[str] = []
    ceiling = ConfidenceLevel.HIGH

    def cap(level: ConfidenceLevel, reason: str) -> None:
        nonlocal ceiling
        reasons.append(reason)
        if _ORDER[level] < _ORDER[ceiling]:
            ceiling = level

    # RULE 1 — nothing to be confident about.
    if stats.usable_thread_count <= 0:
        # Returned immediately: with no corpus, "represented in 0 communities"
        # and "similarity analysis did not run" are restatements of this one
        # fact, and listing them would pad the explanation without adding
        # anything a reader can act on.
        #
        # States the observation, never a cause. This function sees a count of
        # zero and nothing else — it cannot know whether the source was down,
        # the search found nothing, or the fetches failed, and an explanation
        # that guessed would be wrong in three cases out of four. The caller
        # knows which branch it took and supplies that separately; see
        # `evidence_state_reason`.
        cap(
            ConfidenceLevel.LOW,
            "No usable threads were available for synthesis, so there is no "
            "community evidence behind this verdict.",
        )
        return ceiling, reasons

    # RULE 2 — a single thread cannot corroborate itself.
    if stats.usable_thread_count == 1:
        cap(
            ConfidenceLevel.LOW,
            "Only one usable thread was available; independent corroboration "
            "is not possible.",
        )

    # RULE 3 — a narrow spread of communities cannot be called high.
    #
    # The claim is about SPREAD, not independence: this counts distinct
    # subreddit names, and names cannot prove that the communities reached
    # their view separately. Worded accordingly.
    represented = len(stats.represented_subreddits)
    if represented < MIN_SUBREDDITS_FOR_HIGH:
        cap(
            ConfidenceLevel.MODERATE,
            f"Evidence is represented in {represented} "
            f"{'community' if represented == 1 else 'communities'}; high "
            f"confidence requires evidence spanning at least "
            f"{MIN_SUBREDDITS_FOR_HIGH} communities.",
        )

    # RULE 4 — too little strong evidence to stand on strong evidence alone.
    #
    # Reports the count that was observed, never the target it fell short of:
    # the target lives in apply_signal_filters and is not this module's to
    # restate. Says nothing about what was admitted instead, because relaxing
    # does not imply weaker threads exist — four strong threads and no weak
    # ones still relax.
    if stats.filters_relaxed:
        cap(
            ConfidenceLevel.MODERATE,
            f"Only {stats.strong_thread_count} "
            f"{'thread' if stats.strong_thread_count == 1 else 'threads'} "
            "cleared the engagement quality bar — too few to rely on strong "
            "evidence alone.",
        )

    # RULE 5 — near-identical text under different names.
    #
    # A CONSERVATIVE SAFETY POLICY, not a finding of manipulation. Near
    # duplication across authors has innocent explanations (quoting, reposting,
    # a shared source article), and this detector cannot tell those apart from
    # anything else — nothing here establishes that sources are or are not
    # independent, and the system does not claim to have verified that.
    #
    # What it does establish is narrower and sufficient: these items cannot
    # safely be counted as separate corroborating signals. Because HIGH is
    # meant to require stronger corroboration than the rest, that alone is
    # reason to withhold it.
    if stats.cross_author_duplicate_count > 0:
        cap(
            ConfidenceLevel.MODERATE,
            "Near-duplicate content appeared under different authors, so those "
            "items cannot be counted as separate supporting evidence.",
        )

    # RULE 6 — a structural check that did not run cannot be claimed as passed.
    #
    # Deliberately not LOW: an embeddings outage says nothing about the threads
    # themselves, and downgrading real evidence over an infrastructure failure
    # would be its own kind of dishonesty. It does, however, mean one of the
    # anti-manipulation checks behind a HIGH claim never executed.
    if not stats.similarity_analysis_available:
        cap(
            ConfidenceLevel.MODERATE,
            "Similarity analysis was unavailable, so independent-source "
            "quality could not be fully verified.",
        )

    # RULE 8 — evidence nobody checked was even about the question.
    #
    # Reddit search is literal keyword matching, so a retrieval set routinely
    # contains threads that share a word with the query and nothing else. When
    # the relevance screen runs, those are gone before anything is read and no
    # rule is needed. When it could NOT run, the corpus may contain them and
    # every count in this module is measuring an unknown mixture.
    #
    # Same shape and same reasoning as RULE 6: not LOW, because a screening
    # outage says nothing about the threads that ARE relevant, but a check
    # standing behind a HIGH claim did not execute, so HIGH cannot be claimed.
    if not stats.relevance_screened:
        cap(
            ConfidenceLevel.MODERATE,
            "Retrieved threads could not be screened for relevance, so this "
            "corpus may include discussions that do not bear on the question.",
        )

    return ceiling, reasons


# Why a brief has no evidence behind it, one truthful sentence per branch.
#
# `structural_ceiling` deliberately cannot answer this: it sees a thread count
# of zero and has no way to distinguish a blocked source from an empty search
# from a corpus miss. Emitting one generic sentence for all of them was wrong
# in most cases — it blamed retrieval and filtering for outcomes that were
# decided long before either ran. The caller knows which branch it took, so
# the caller supplies the reason.
_EVIDENCE_STATE_REASONS: dict[EvidenceState, str] = {
    EvidenceState.NOT_IN_CORPUS: (
        "This question is not available in the frozen research corpus, so no "
        "evidence could be gathered for it."
    ),
    EvidenceState.SOURCE_UNAVAILABLE: (
        "The research source was unavailable, so no evidence corpus could be "
        "built for this question."
    ),
    EvidenceState.NO_EVIDENCE: (
        "No relevant community threads were found for this question."
    ),
    EvidenceState.NO_USABLE_EVIDENCE: (
        "Candidate discussions were found, but no full thread evidence could "
        "be retrieved for synthesis."
    ),
}


def evidence_state_reason(state: EvidenceState) -> list[str]:
    """The deterministic explanation for a no-evidence brief.

    Returns a list so callers can assign it straight to `confidence_reasons`,
    and an empty one for `OK` — a brief with a corpus is explained by the
    ceiling rules instead.
    """
    reason = _EVIDENCE_STATE_REASONS.get(state)
    return [reason] if reason else []


def resolve_confidence(
    semantic: ConfidenceLevel,
    ceiling: ConfidenceLevel,
    has_consensus_pick: bool,
) -> ConfidenceLevel:
    """The final verdict: the more conservative of model and structure.

    RULE 7 — a brief with no consensus pick is LOW whatever else happened.
    When one half of the split synthesis fails, the surviving half can still
    return a confidence for a brief that recommends nothing; "high confidence
    in no recommendation" is not a coherent thing to show anyone.
    """
    if not has_consensus_pick:
        return ConfidenceLevel.LOW
    return semantic if _ORDER[semantic] <= _ORDER[ceiling] else ceiling


def assess(semantic: ConfidenceLevel, stats: EvidenceStats) -> ConfidenceOutcome:
    """Full confidence decision for one brief: the composition used in the
    pipeline, kept here so the reasons can never drift from the verdict they
    explain."""
    ceiling, reasons = structural_ceiling(stats)
    final = resolve_confidence(semantic, ceiling, stats.has_consensus_pick)

    if not stats.has_consensus_pick:
        reasons.append(
            "No consensus pick was produced, so there is no recommendation to "
            "be confident about."
        )
    elif _ORDER[semantic] < _ORDER[ceiling]:
        # The structural rules did not bind here, and saying they did would
        # blame the evidence for the model's caution. Name what actually
        # happened instead.
        reasons.append(
            "The semantic analysis was more cautious than the structural "
            "evidence ceiling."
        )

    return ConfidenceOutcome(
        semantic=semantic,
        ceiling=ceiling,
        final=final,
        reasons=tuple(reasons),
    )
