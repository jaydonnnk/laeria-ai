"""The deterministic confidence policy — Phase A.

These tests are the specification for what evidence is allowed to earn. They
exercise behaviour and invariants, never internals: nothing here asserts which
rule object fired or in what order, only what a caller can observe.

Pure module, so there is no network, no OpenRouter, no Reddit, no Supabase and
no fixture corpus involved in any of it.
"""

from __future__ import annotations

import itertools

import pytest

from agents.confidence import (
    MIN_SUBREDDITS_FOR_HIGH,
    EvidenceStats,
    assess,
    resolve_confidence,
    structural_ceiling,
)
from core.models import ConfidenceLevel

HIGH = ConfidenceLevel.HIGH
MODERATE = ConfidenceLevel.MODERATE
LOW = ConfidenceLevel.LOW

ALL_LEVELS = (LOW, MODERATE, HIGH)


def strong_evidence(**overrides) -> EvidenceStats:
    """The strongest legal evidence shape: nothing should cap this.

    Every adversarial case below is this shape with ONE thing degraded, so a
    failure names the rule that broke rather than a soup of conditions.
    """
    base = dict(
        usable_thread_count=8,
        strong_thread_count=8,
        represented_subreddits=("SteamDeck", "HandheldPC", "patientgamers", "gaming"),
        filters_relaxed=False,
        cross_author_duplicate_count=0,
        similarity_analysis_available=True,
        relevance_screened=True,
        has_consensus_pick=True,
    )
    base.update(overrides)
    return EvidenceStats(**base)


# ---- ordering ------------------------------------------------------------
#
# ConfidenceLevel is a str Enum, so `HIGH < LOW` is True by string comparison.
# Any implementation that leans on that is silently inverted, and every other
# test in this file would still pass. Hence testing the ordering directly.


def test_confidence_is_not_ordered_by_its_string_values():
    """Guards the trap: alphabetically "high" < "low" < "moderate"."""
    assert HIGH.value < LOW.value < MODERATE.value  # the trap, as documented

    # The policy must not inherit that ordering.
    assert resolve_confidence(HIGH, LOW, True) is LOW
    assert resolve_confidence(LOW, HIGH, True) is LOW
    assert resolve_confidence(MODERATE, HIGH, True) is MODERATE


@pytest.mark.parametrize("semantic", ALL_LEVELS)
@pytest.mark.parametrize("ceiling", ALL_LEVELS)
def test_final_never_exceeds_either_input(semantic, ceiling):
    """The two core invariants, over the whole 3x3 space."""
    rank = {LOW: 0, MODERATE: 1, HIGH: 2}
    final = resolve_confidence(semantic, ceiling, True)
    assert rank[final] <= rank[semantic], "final exceeded the model's judgement"
    assert rank[final] <= rank[ceiling], "final exceeded the structural ceiling"
    assert final in (semantic, ceiling), "final invented a level neither side proposed"


# ---- the required minimum cases -----------------------------------------


def test_case1_strong_evidence_and_a_confident_model_stays_high():
    """The benchmark would be worthless if every case expected LOW."""
    outcome = assess(HIGH, strong_evidence())
    assert outcome.final is HIGH
    assert outcome.ceiling is HIGH
    assert outcome.reasons == (), "a clean corpus should produce no limitations"


def test_case2_structure_never_upgrades_a_moderate_model():
    outcome = assess(MODERATE, strong_evidence())
    assert outcome.ceiling is HIGH
    assert outcome.final is MODERATE


def test_case3_structure_never_upgrades_a_low_model():
    outcome = assess(LOW, strong_evidence())
    assert outcome.ceiling is HIGH
    assert outcome.final is LOW


def test_case4_a_single_thread_can_never_be_better_than_low():
    outcome = assess(HIGH, strong_evidence(usable_thread_count=1, strong_thread_count=1))
    assert outcome.final is LOW


def test_case5_one_community_caps_at_moderate():
    outcome = assess(HIGH, strong_evidence(represented_subreddits=("SteamDeck",)))
    assert outcome.final is MODERATE


def test_case6_two_communities_cap_at_moderate():
    outcome = assess(
        HIGH, strong_evidence(represented_subreddits=("SteamDeck", "HandheldPC"))
    )
    assert outcome.final is MODERATE


def test_case7_three_communities_may_remain_high():
    outcome = assess(
        HIGH,
        strong_evidence(represented_subreddits=("SteamDeck", "HandheldPC", "gaming")),
    )
    assert outcome.final is HIGH


def test_case8_relaxed_filters_cap_at_moderate():
    outcome = assess(HIGH, strong_evidence(filters_relaxed=True, strong_thread_count=2))
    assert outcome.final is MODERATE


def test_case9_cross_author_duplicates_cap_at_moderate():
    outcome = assess(HIGH, strong_evidence(cross_author_duplicate_count=1))
    assert outcome.final is MODERATE


def test_case10_unavailable_similarity_analysis_caps_at_moderate():
    """A structural check that did not run cannot back a HIGH claim."""
    outcome = assess(HIGH, strong_evidence(similarity_analysis_available=False))
    assert outcome.final is MODERATE


def test_case10b_unavailable_similarity_analysis_does_not_force_low():
    """An embeddings outage says nothing about the threads themselves."""
    outcome = assess(HIGH, strong_evidence(similarity_analysis_available=False))
    assert outcome.final is MODERATE, "an infrastructure failure must not read as bad evidence"


def test_an_unscreened_corpus_caps_at_moderate():
    """RULE 8: nobody checked these threads were even about the question.

    Reddit search is keyword matching, so an unscreened corpus can contain
    threads that share a word with the query and nothing else. Every count in
    the policy is then measuring an unknown mixture.
    """
    outcome = assess(HIGH, strong_evidence(relevance_screened=False))
    assert outcome.final is MODERATE
    assert any("relevance" in r.lower() for r in outcome.reasons)


def test_an_unscreened_corpus_does_not_force_low():
    """Same shape as the similarity rule: a check outage is not bad evidence."""
    outcome = assess(HIGH, strong_evidence(relevance_screened=False))
    assert outcome.final is MODERATE
    outcome_moderate = assess(MODERATE, strong_evidence(relevance_screened=False))
    assert outcome_moderate.final is MODERATE


def test_a_screened_corpus_is_not_penalised():
    """The rule must fire on the outage only, never on the normal path."""
    ceiling, reasons = structural_ceiling(strong_evidence(relevance_screened=True))
    assert ceiling is HIGH
    assert not any("relevance" in r.lower() for r in reasons)


def test_case11_an_empty_consensus_pick_forces_low():
    outcome = assess(HIGH, strong_evidence(has_consensus_pick=False))
    assert outcome.final is LOW


def test_case11b_an_empty_pick_beats_even_a_high_ceiling():
    """Closes the half-failed-synthesis incoherence end to end."""
    stats = strong_evidence(has_consensus_pick=False)
    ceiling, _ = structural_ceiling(stats)
    assert ceiling is HIGH, "structure alone sees nothing wrong with this corpus"
    assert resolve_confidence(HIGH, ceiling, stats.has_consensus_pick) is LOW


def test_case12_multiple_caps_yield_the_most_conservative():
    outcome = assess(
        HIGH,
        strong_evidence(
            usable_thread_count=1,            # LOW
            represented_subreddits=("a",),    # MODERATE
            filters_relaxed=True,             # MODERATE
            cross_author_duplicate_count=2,   # MODERATE
            similarity_analysis_available=False,  # MODERATE
        ),
    )
    assert outcome.final is LOW, "the strictest fired rule must win"


def test_case13_no_rule_fires_leaves_a_high_ceiling():
    ceiling, reasons = structural_ceiling(strong_evidence())
    assert ceiling is HIGH
    assert reasons == []


def test_case14_reasons_name_only_rules_that_actually_fired():
    _, reasons = structural_ceiling(strong_evidence(filters_relaxed=True, strong_thread_count=3))
    assert len(reasons) == 1, f"expected exactly the relaxation reason, got {reasons}"
    joined = reasons[0].lower()
    # Asserted on substance rather than a particular verb: the reason must
    # identify the engagement bar and the count that fell short of it.
    assert "engagement quality bar" in joined
    assert "3 threads" in joined
    # Rules that did not fire must not appear.
    assert "duplicate" not in joined
    assert "similarity" not in joined
    assert "one usable thread" not in joined


def test_no_usable_threads_is_low_and_says_so_once():
    ceiling, reasons = structural_ceiling(EvidenceStats(usable_thread_count=0))
    assert ceiling is LOW
    assert len(reasons) == 1, "an empty corpus should not be explained five ways"


# ---- adversarial / falsification ----------------------------------------


def test_default_stats_fail_closed():
    """A partially-populated struct must never read as strong evidence."""
    assert structural_ceiling(EvidenceStats())[0] is LOW
    assert assess(HIGH, EvidenceStats()).final is LOW


def test_weakest_legal_evidence_cannot_reach_high():
    """Semantic HIGH against every single-degradation shape."""
    degradations = [
        {"usable_thread_count": 1, "strong_thread_count": 1},
        {"represented_subreddits": ("only",)},
        {"represented_subreddits": ("a", "b")},
        {"filters_relaxed": True},
        {"cross_author_duplicate_count": 1},
        {"similarity_analysis_available": False},
        {"relevance_screened": False},
        {"has_consensus_pick": False},
    ]
    for degradation in degradations:
        outcome = assess(HIGH, strong_evidence(**degradation))
        assert outcome.final is not HIGH, f"{degradation} still reached HIGH"


def test_structure_can_never_raise_any_semantic_verdict():
    """Exhaustive: no evidence shape in the reachable space promotes a model.

    This is the invariant a judge would attack — that the ceiling might be
    doing the model's job for it.
    """
    space = itertools.product(
        (0, 1, 2, 8),          # usable threads
        ((), ("a",), ("a", "b"), ("a", "b", "c")),  # represented subreddits
        (False, True),         # filters relaxed
        (0, 3),                # cross-author duplicates
        (False, True),         # similarity available
        (False, True),         # relevance screened
        (False, True),         # has pick
    )
    rank = {LOW: 0, MODERATE: 1, HIGH: 2}
    for threads, subs, relaxed, dupes, sim, screened, pick in space:
        stats = EvidenceStats(
            usable_thread_count=threads,
            strong_thread_count=threads,
            represented_subreddits=subs,
            filters_relaxed=relaxed,
            cross_author_duplicate_count=dupes,
            similarity_analysis_available=sim,
            relevance_screened=screened,
            has_consensus_pick=pick,
        )
        for semantic in ALL_LEVELS:
            outcome = assess(semantic, stats)
            assert rank[outcome.final] <= rank[semantic], (
                f"structure promoted {semantic} to {outcome.final} for {stats}"
            )


def test_high_requires_every_condition_simultaneously():
    """The only route to HIGH is a model saying HIGH over a clean corpus."""
    rank = {LOW: 0, MODERATE: 1, HIGH: 2}
    space = itertools.product(
        (1, 2, 5),
        ((), ("a",), ("a", "b"), ("a", "b", "c")),
        (False, True),
        (0, 1),
        (False, True),
        (False, True),
        (False, True),
    )
    reached_high = 0
    for threads, subs, relaxed, dupes, sim, screened, pick in space:
        stats = EvidenceStats(
            usable_thread_count=threads,
            strong_thread_count=threads,
            represented_subreddits=subs,
            filters_relaxed=relaxed,
            cross_author_duplicate_count=dupes,
            similarity_analysis_available=sim,
            relevance_screened=screened,
            has_consensus_pick=pick,
        )
        for semantic in ALL_LEVELS:
            if assess(semantic, stats).final is not HIGH:
                continue
            reached_high += 1
            assert semantic is HIGH
            assert threads > 1
            assert len(subs) >= MIN_SUBREDDITS_FOR_HIGH
            assert not relaxed
            assert dupes == 0
            assert sim
            assert screened
            assert pick
            assert rank[semantic] == 2
    # Guards the test itself: adding a rule whose default forbids HIGH would
    # otherwise leave this loop asserting nothing and still passing.
    assert reached_high > 0, "no shape in the space reached HIGH — this test proved nothing"


def test_a_cautious_model_is_explained_without_blaming_the_evidence():
    outcome = assess(LOW, strong_evidence())
    assert outcome.final is LOW
    joined = " ".join(outcome.reasons).lower()
    assert "cautious" in joined
    # Structure found nothing wrong, so it must not be implied that it did.
    assert "relax" not in joined
    assert "duplicate" not in joined


def test_a_capped_model_is_not_described_as_cautious():
    outcome = assess(HIGH, strong_evidence(represented_subreddits=("a", "b")))
    joined = " ".join(outcome.reasons).lower()
    assert "cautious" not in joined
    assert "communities" in joined


def test_outcome_reports_all_three_values_for_the_ui():
    outcome = assess(HIGH, strong_evidence(represented_subreddits=("a",)))
    assert outcome.semantic is HIGH
    assert outcome.ceiling is MODERATE
    assert outcome.final is MODERATE


def test_reasons_do_not_claim_the_count_proves_independence():
    """CASE 5: distinct subreddit NAMES are a proxy, not proof.

    A claim can be repeated across several communities from one upstream
    source, and no count can see that. The wording must describe spread, and
    leave independence to the model's semantic judgement.
    """
    _, reasons = structural_ceiling(strong_evidence(represented_subreddits=("a", "b")))
    text = " ".join(reasons)
    assert "spanning at least 3 communities" in text
    assert "independent" not in text, (
        f"claimed the subreddit count proves independence: {text!r}"
    )


def test_three_communities_still_clears_the_rule_after_the_rewording():
    """The ceiling itself must not have been weakened by the copy change."""
    ceiling, reasons = structural_ceiling(
        strong_evidence(represented_subreddits=("a", "b", "c"))
    )
    assert ceiling is HIGH
    assert reasons == []


def test_the_relaxation_reason_does_not_restate_the_target_threshold():
    """The '5' lives in apply_signal_filters and must not be mirrored here.

    Two copies of one policy number drift the moment either is tuned, and the
    explanation would then describe a threshold no longer in force.
    """
    import agents.confidence as confidence_module

    _, reasons = structural_ceiling(
        strong_evidence(filters_relaxed=True, strong_thread_count=4)
    )
    text = " ".join(reasons)
    assert "4 threads cleared" in text, "the observed count should be reported"
    assert "5" not in text, f"restated the target threshold: {text!r}"
    assert not hasattr(confidence_module, "STRONG_THREAD_TARGET"), (
        "the pure policy module should not own a copy of the filter threshold"
    )


def test_the_relaxation_reason_does_not_claim_weaker_threads_were_included():
    """CASE 3: four strong threads and no weak ones still relaxes.

    Relaxation means too few threads cleared the bar — not that anything
    weaker was admitted, because there may be nothing weaker to admit.
    """
    _, reasons = structural_ceiling(
        strong_evidence(usable_thread_count=4, strong_thread_count=4, filters_relaxed=True)
    )
    text = " ".join(reasons).lower()
    assert "weaker" not in text and "weak threads" not in text, (
        f"claimed weaker evidence was included: {text!r}"
    )


def test_a_single_cleared_thread_reads_as_singular():
    _, reasons = structural_ceiling(
        strong_evidence(filters_relaxed=True, strong_thread_count=1)
    )
    assert "Only 1 thread cleared" in " ".join(reasons)


def test_reasons_are_plain_sentences_not_codes():
    """The UI renders these verbatim, so they must read as English."""
    outcome = assess(HIGH, strong_evidence(usable_thread_count=1))
    for reason in outcome.reasons:
        assert reason[0].isupper(), f"not a sentence: {reason!r}"
        assert reason.endswith("."), f"not a sentence: {reason!r}"
        assert "_" not in reason, f"leaked an identifier: {reason!r}"


def test_stats_are_immutable():
    """The ceiling must not be mutable after the fact by a later caller."""
    stats = strong_evidence()
    with pytest.raises(Exception):
        stats.usable_thread_count = 99  # type: ignore[misc]


def test_no_percentages_or_scores_are_produced():
    outcome = assess(HIGH, strong_evidence(filters_relaxed=True, strong_thread_count=2))
    assert not hasattr(outcome, "score")
    for reason in outcome.reasons:
        assert "%" not in reason, "the policy must not claim numeric precision"
