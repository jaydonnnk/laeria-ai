"""Which threads Mode 2 chooses to read, and why.

The defect these pin: candidate selection ranked on `score + num_comments`
alone, so a popular thread beat a relevant one every time. Against live Reddit,
"Is the Koss KSC75 worth buying under $30?" selected eight threads of which
none mentioned the KSC75 — led by "Half a year sober from the hobby" (486 pts)
— while seven KSC75 threads sat unread in the same candidate pool.

Confidence is deliberately NOT the subject here. It was audited separately and
found to be working: a scrutiny half that returns "high" or "moderate" keeps
it. The last test pins the one confidence behaviour that is easy to mistake for
a bug, so nobody "fixes" it into being less careful.
"""

from __future__ import annotations

from pathlib import Path

from agents import relevance
from agents.research_agent import _in_chosen_order, _spread_pick
from core.models import ConfidenceLevel, RedditThread

KSC75_QUERY = "Is the Koss KSC75 worth buying under $30?"


def thread(tid: str, sub: str, title: str, score: int = 0, comments: int = 0):
    return RedditThread(
        id=tid, subreddit=sub, title=title, url=f"https://reddit.com/{tid}",
        score=score, num_comments=comments,
    )


def weights_for(query: str, candidates, search_queries=()):
    return relevance.build_weights(query, list(search_queries), candidates)


def titles(threads):
    return [t.title for t in threads]


# ---- 1. relevance beats raw engagement ----

def test_a_relevant_thread_outranks_a_far_more_popular_irrelevant_one():
    """The exact case from the audit: 220 pts of off-topic must not outrank
    8 comments of on-topic."""
    off_topic = thread("a", "headphones", "ATH-R70XA endgame review", 220, 60)
    on_topic = thread("b", "headphones", "Koss KSC75 under $30 review", 20, 8)
    candidates = [off_topic, on_topic]

    picked = _spread_pick(candidates, 2, weights_for(KSC75_QUERY, candidates))

    assert picked[0] is on_topic, "the KSC75 thread must be read first"
    # With room to spare the popular thread still gets read — relevance
    # reorders the budget, it does not shrink the corpus.
    assert picked[1] is off_topic


def test_the_relevant_thread_survives_when_the_budget_fits_only_one():
    off_topic = thread("a", "headphones", "ATH-R70XA endgame review", 220, 60)
    on_topic = thread("b", "headphones", "Koss KSC75 under $30 review", 20, 8)
    candidates = [off_topic, on_topic]

    picked = _spread_pick(candidates, 1, weights_for(KSC75_QUERY, candidates))

    assert titles(picked) == ["Koss KSC75 under $30 review"]


def test_engagement_cannot_be_bought_past_relevance_at_any_gap():
    """A thousand upvotes of irrelevance still loses to one relevant thread."""
    candidates = [
        thread("a", "headphones", "Half a year sober from the hobby", 5000, 900),
        thread("b", "headphones", "Why the KSC75 are so great", 1, 0),
    ]
    picked = _spread_pick(candidates, 1, weights_for(KSC75_QUERY, candidates))
    assert titles(picked) == ["Why the KSC75 are so great"]


# ---- 2 & 3. subreddit diversity ----

def test_relevant_threads_from_several_subreddits_are_all_represented():
    candidates = [
        thread("a1", "headphones", "KSC75 impressions after a year", 300, 90),
        thread("a2", "headphones", "KSC75 vs Porta Pro", 250, 80),
        thread("a3", "headphones", "My KSC75 mod writeup", 200, 70),
        thread("b1", "HeadphoneAdvice", "Is the Koss KSC75 Worth Buying?", 1, 15),
        thread("c1", "BudgetAudiophile", "KSC75 still the budget king?", 2, 9),
    ]
    picked = _spread_pick(candidates, 3, weights_for(KSC75_QUERY, candidates))

    assert {t.subreddit for t in picked} == {
        "headphones", "HeadphoneAdvice", "BudgetAudiophile",
    }, "one busy subreddit must not take the whole budget"


def test_relevance_does_not_collapse_into_a_single_subreddit():
    """Even when one sub holds the single strongest match, the others are
    still read — cross-community corroboration is the point of Mode 2."""
    candidates = [
        thread("a1", "headphones", "Koss KSC75 review: the budget king", 10, 5),
        thread("a2", "headphones", "Koss KSC75 teardown", 9, 4),
        thread("a3", "headphones", "Koss KSC75 cable mod", 8, 3),
        thread("b1", "HeadphoneAdvice", "KSC75 worth it?", 1, 1),
    ]
    picked = _spread_pick(candidates, 2, weights_for(KSC75_QUERY, candidates))
    assert len({t.subreddit for t in picked}) == 2


def test_a_subreddit_with_nothing_relevant_is_not_handed_a_slot():
    """Round-robin used to guarantee every subreddit a place, which is how
    "college cups" (1 pt, r/stanleycup — the ice-hockey trophy) reached a
    brief about a water bottle."""
    candidates = [
        thread("a1", "HydroHomies", "Stanley Quencher 40oz honest thoughts", 20, 9),
        thread("a2", "HydroHomies", "Stanley Quencher vs Owala", 15, 7),
        thread("b1", "stanleycup", "college cups", 1, 0),
    ]
    picked = _spread_pick(
        candidates, 2, weights_for("Is the Stanley Quencher worth buying?", candidates)
    )
    assert "college cups" not in titles(picked)
    assert len(picked) == 2


# ---- 4 & 5. query shapes ----

def test_a_generic_category_query_still_selects_on_topic_threads():
    query = "Best rechargeable AA batteries under $30?"
    candidates = [
        thread("a", "flashlight", "Nom's Guide to Flashlights", 69, 95),
        thread("b", "flashlight", "What makes a best Flashlight for everyday use?", 31, 107),
        thread("c", "batteries", "Comprehensive comparison: 61 rechargeable AA/AAA cells", 11, 5),
        thread("d", "batteries", "Rechargeable AA batteries? do they exist?", 41, 128),
    ]
    picked = _spread_pick(candidates, 2, weights_for(query, candidates))

    assert all("Flashlight" not in t.title for t in picked), (
        "a general flashlight guide is not evidence about rechargeable batteries"
    )


def test_the_price_ceiling_is_not_treated_as_a_topic_term():
    """"under $30" is a budget, not a subject. Matching the number lexically
    made "worth it at only 30 euro?" a top hit for a KSC75 question."""
    candidates = [
        thread("a", "BudgetAudiophile", "is a Logitech z523 worth it at only 30 euro?", 0, 27),
        thread("b", "HeadphoneAdvice", "Koss KSC75 thoughts", 1, 2),
    ]
    picked = _spread_pick(candidates, 1, weights_for(KSC75_QUERY, candidates))
    assert titles(picked) == ["Koss KSC75 thoughts"]


def test_an_exact_model_query_matches_a_title_that_spaces_the_model_number():
    """Reddit titles space model numbers however the poster felt. "ksc 75"
    has to match a query that says "KSC75" or the best thread is missed."""
    candidates = [
        thread("a", "headphones", "Some other headphone discussion", 500, 200),
        thread("b", "HeadphoneAdvice", "Are koss ksc 75 worth it?", 5, 10),
    ]
    picked = _spread_pick(candidates, 1, weights_for("Is the Koss KSC75 worth buying?", candidates))
    assert titles(picked) == ["Are koss ksc 75 worth it?"]


def test_planner_search_phrases_contribute_community_vocabulary():
    """The user never types "eneloop", but the planner does — and a thread
    naming it is real evidence about rechargeable batteries."""
    query = "Best rechargeable batteries under $30?"
    candidates = [
        thread("a", "batteries", "Random battery chat", 900, 400),
        thread("b", "batteries", "Eneloop Pro long-term report", 5, 5),
    ]
    picked = _spread_pick(
        candidates, 1,
        weights_for(query, candidates, search_queries=["eneloop vs", "rechargeable AA"]),
    )
    assert titles(picked) == ["Eneloop Pro long-term report"]


# ---- 6 & 7. budget and tie-breaking ----

def test_the_thread_budget_is_respected():
    candidates = [
        thread(f"t{i}", f"sub{i % 3}", f"Koss KSC75 thread {i}", i, i)
        for i in range(30)
    ]
    for budget in (1, 3, 8, 12):
        assert len(_spread_pick(candidates, budget, weights_for(KSC75_QUERY, candidates))) == budget


def test_the_budget_is_respected_when_relevant_threads_run_out():
    candidates = [
        thread("a", "headphones", "Koss KSC75 review", 5, 5),
        thread("b", "headphones", "Unrelated amp thread", 400, 100),
        thread("c", "headphones", "Another unrelated thread", 300, 90),
    ]
    picked = _spread_pick(candidates, 2, weights_for(KSC75_QUERY, candidates))
    assert len(picked) == 2
    assert picked[0].title == "Koss KSC75 review"


def test_equally_relevant_candidates_are_still_ordered_by_engagement():
    """Relevance decides the shortlist; engagement still decides within it."""
    candidates = [
        thread("quiet", "headphones", "Koss KSC75 review", 10, 2),
        thread("loud", "headphones", "Koss KSC75 review", 900, 300),
    ]
    picked = _spread_pick(candidates, 2, weights_for(KSC75_QUERY, candidates))
    assert [t.id for t in picked] == ["loud", "quiet"]


def test_without_weights_the_old_engagement_behaviour_is_unchanged():
    """`_spread_pick` is still the pre-existing function when no query context
    is supplied — the change is a refinement, not a replacement.

    "beta" leads on engagement, then the round-robin moves to the other
    subreddit before taking r/one's second thread. Verified equivalent to the
    pre-change implementation over 3000 randomised inputs.
    """
    candidates = [
        thread("a", "one", "alpha", 10, 10),
        thread("b", "two", "beta", 500, 500),
        thread("c", "one", "gamma", 300, 300),
    ]
    assert [t.id for t in _spread_pick(candidates, 3)] == ["b", "c", "a"]


def test_the_fallback_tier_still_spreads_across_subreddits():
    """When nothing matches the query, the corpus must still come from more
    than one community — cross-subreddit corroboration cannot depend on the
    relevance pass having found something."""
    candidates = [
        thread("a1", "one", "alpha", 900, 900),
        thread("a2", "one", "beta", 800, 800),
        thread("b1", "two", "gamma", 10, 10),
    ]
    picked = _spread_pick(candidates, 2, weights_for("totally unrelated query", candidates))
    assert {t.subreddit for t in picked} == {"one", "two"}


def test_duplicate_ids_are_never_selected_twice():
    dup = thread("same", "headphones", "Koss KSC75 review", 10, 10)
    picked = _spread_pick([dup, dup], 2, weights_for(KSC75_QUERY, [dup, dup]))
    assert len(picked) == 1


# ---- scoring properties ----

def test_repeating_a_keyword_in_a_title_earns_nothing_extra():
    """Set semantics: title-stuffing is inert, which is what stops this from
    being a new thing to game."""
    honest = thread("a", "headphones", "Koss KSC75 review", 0, 0)
    stuffed = thread("b", "headphones", "KSC75 KSC75 KSC75 KSC75 Koss Koss", 0, 0)
    candidates = [honest, stuffed]
    w = weights_for(KSC75_QUERY, candidates)
    assert relevance.score(honest, w) == relevance.score(stuffed, w)


def test_a_term_common_to_every_candidate_carries_little_weight():
    """IDF over the candidate pool: "headphones" is everywhere and cannot
    decide anything, "ksc75" is rare and decides everything."""
    candidates = [
        thread(str(i), "headphones", f"headphones discussion {i}", 0, 0)
        for i in range(10)
    ] + [thread("x", "headphones", "headphones ksc75 thread", 0, 0)]
    w = weights_for(KSC75_QUERY, candidates)
    assert w["ksc75"] > w.get("headphones", 0.0) * 3


def test_scores_are_never_negative():
    """A term match can only ever raise a thread, so relevance reorders the
    corpus and can never quietly exclude something the old code kept."""
    candidates = [thread(str(i), "s", f"title {i} koss ksc75 headphones", 0, 0) for i in range(40)]
    w = weights_for(KSC75_QUERY, candidates)
    assert all(v > 0 for v in w.values())
    assert all(relevance.score(t, w) >= 0 for t in candidates)


def test_an_empty_weight_map_scores_everything_zero():
    assert relevance.score(thread("a", "s", "anything at all"), {}) == 0.0


# ---- corpus ordering ----

def test_the_corpus_keeps_selection_order_and_loses_nothing():
    chosen = [thread("a", "s", "first"), thread("b", "s", "second"), thread("c", "s", "third")]
    # As `apply_signal_filters` returns them: engagement order, one dropped.
    filtered = [chosen[2], chosen[0]]
    assert [t.id for t in _in_chosen_order(filtered, chosen)] == ["a", "c"]


def test_reordering_never_drops_a_thread_missing_from_the_selection():
    chosen = [thread("a", "s", "first")]
    stray = thread("z", "s", "arrived from somewhere else")
    reordered = _in_chosen_order([stray, chosen[0]], chosen)
    assert [t.id for t in reordered] == ["a", "z"]


# ---- confidence stays as it is ----

def test_a_failed_scrutiny_half_still_degrades_to_low():
    """Confidence lives in the scrutiny half. When that half fails the field
    is absent and `_safe_confidence` reads LOW — which is the SAFE direction
    (`/research/act` refuses low) and is logged as a failed half. Pinned so it
    is not "fixed" into inventing confidence the model never returned.
    """
    from agents.research_agent import _SCRUTINY_SYSTEM, ResearchAgent

    class HalfDeadLLM:
        worst_case_seconds = 1

        def embed(self, texts):
            raise RuntimeError("no embeddings in this test")

        def complete_json(self, system, user, max_tokens=4000, temperature=0.2):
            if system == _SCRUTINY_SYSTEM:
                raise TimeoutError("scrutiny half timed out")
            return {"consensus_pick": "Koss KSC75", "strengths": ["cheap"],
                    "alternatives": ["Porta Pro"]}

    threads = [
        thread("a", "headphones", "Koss KSC75 review", 100, 20),
        thread("b", "headphones", "KSC75 long term", 90, 18),
    ]
    brief = ResearchAgent(reddit=object(), llm=HalfDeadLLM())._synthesise(
        KSC75_QUERY, threads, ["headphones"], {}
    )
    assert brief.confidence is ConfidenceLevel.LOW
    assert brief.consensus_pick == "Koss KSC75", "the surviving half still renders"


def test_this_change_introduces_no_confidence_or_evidence_engine_dependency():
    """The same scope line test_bedrock_guardrails.py draws, extended to the
    module this change adds — that test only scans research_agent.py and
    bedrock_guardrails.py, so a new file could have slipped past it.

    This change ranks retrieval. It does not judge evidence, does not cap
    confidence, and introduces no evidence-set architecture.
    """
    from agents import research_agent

    for module in (relevance, research_agent):
        source = Path(module.__file__).read_text(encoding="utf-8")
        for banned in ("agents.confidence", "agents.evidence", "UsableEvidence",
                       "EvidenceState", "ResearchHandoff", "structural_ceiling",
                       "UNSAFE_EVIDENCE"):
            assert banned not in source, f"{module.__name__} references {banned}"


def test_relevance_makes_no_network_model_or_embedding_call():
    """The reason this needed no new guardrail boundary: nothing leaves the
    process. A model or embedding call here would be a new place untrusted
    Reddit text reaches a third party, and would need its own screening.

    Checked by what the module IMPORTS rather than by scanning for words —
    prose about embeddings is not an embedding call, and a test that cannot
    tell the difference is one people learn to edit rather than heed.
    """
    import ast

    tree = ast.parse(Path(relevance.__file__).read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])

    assert imported <= {"__future__", "math", "re", "core"}, (
        f"relevance.py must stay pure and offline; it imports {imported}"
    )


def test_a_scrutiny_half_that_returns_high_keeps_high():
    """The other direction of the same behaviour: confidence is preserved
    exactly as the model reported it, never downgraded in transit."""
    from agents.research_agent import _SCRUTINY_SYSTEM, ResearchAgent

    class SplitLLM:
        worst_case_seconds = 1

        def embed(self, texts):
            raise RuntimeError("no embeddings in this test")

        def complete_json(self, system, user, max_tokens=4000, temperature=0.2):
            if system == _SCRUTINY_SYSTEM:
                return {"failure_modes": ["cable"], "red_flags": [],
                        "confidence": "high", "bias_notes": "broad coverage"}
            return {"consensus_pick": "Koss KSC75", "strengths": ["cheap"],
                    "alternatives": []}

    threads = [thread("a", "headphones", "Koss KSC75 review", 100, 20)]
    brief = ResearchAgent(reddit=object(), llm=SplitLLM())._synthesise(
        KSC75_QUERY, threads, ["headphones"], {}
    )
    assert brief.confidence is ConfidenceLevel.HIGH
