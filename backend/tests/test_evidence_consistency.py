"""One brief, one evidence set — and nothing else may contradict it.

Two failures found in manual testing, both of the same kind: a fact about the
evidence was produced somewhere other than the evidence itself.

1. A LASIK query rendered "8 threads across r/lasik, r/eyetriage, r/optometry,
   r/vision" and, on the same screen, a red flag reading "all evidence from a
   single subreddit (r/lasik) — no cross-community corroboration". The counts
   came from the corpus; the red flag came from the model, and nothing checked
   it against the corpus.

2. A "keyboard under $100 SGD" query retrieved "where to go to buy laptops in
   SG" and "best quiet area in SG for long term stay". Those threads were
   popular, so they cleared the engagement filters, and then counted as usable
   evidence — inflating the thread count, the represented communities, and the
   confidence that evidence shape was allowed to earn.

These tests hold both doors shut, driving the real pipeline through fakes: no
network, no OpenRouter, no Reddit, no Supabase, no fixture corpus.
"""
from __future__ import annotations

import pytest

from agents.evidence import UsableEvidence, contradicts, verified_claims
from agents.research_agent import ResearchAgent
from core.models import ConfidenceLevel, EvidenceState
from tests.fakes import FakeLLM, FakeReddit, strong_corpus, thread

HIGH = ConfidenceLevel.HIGH
MODERATE = ConfidenceLevel.MODERATE
LOW = ConfidenceLevel.LOW

RANK = {LOW: 0, MODERATE: 1, HIGH: 2}

# The exact sentence the LASIK run displayed beside a four-community count.
LASIK_RED_FLAG = (
    "All evidence from a single subreddit (r/lasik) — no cross-community "
    "corroboration"
)


def _run(agent: ResearchAgent, query: str = "is the Steam Deck OLED worth it"):
    # use_cache=False: a cache hit would bypass the pipeline under test.
    return agent.synthesise_decision(query, use_cache=False)


def _agent(reddit=None, llm=None) -> ResearchAgent:
    return ResearchAgent(reddit=reddit or FakeReddit(), llm=llm or FakeLLM())


# ---- the keyboard corpus: real hits mixed with popular off-topic threads ----

KEYBOARD_SUBS = ["MechanicalKeyboards", "buildapc", "singapore", "askSingapore"]

RELEVANT = [
    thread("kb1", "MechanicalKeyboards", title="cheap keyboard under $100?"),
    thread("kb2", "MechanicalKeyboards", title="best budget switches"),
    thread("kb3", "buildapc", title="budget board recommendations SG"),
    thread("kb4", "buildapc", title="which keyboard for $100"),
]

# Off-topic, and every one of them a big popular thread — they clear the
# engagement bar comfortably, which is precisely why an engagement filter
# alone could never keep them out.
OFF_TOPIC = [
    thread("x1", "singapore", 900, 400, title="where to go to buy laptops in SG"),
    thread("x2", "singapore", 800, 300, title="best quiet area in SG for long term stay"),
    thread("x3", "askSingapore", 700, 250, title="cheapest place for groceries"),
    thread("x4", "askSingapore", 600, 200, title="how to renew a passport"),
]

RELEVANT_IDS = [t.id for t in RELEVANT]


def _keyboard_agent(candidates, relevant_ids=RELEVANT_IDS, **llm_kw) -> ResearchAgent:
    return ResearchAgent(
        reddit=FakeReddit(candidates=candidates),
        llm=FakeLLM(subreddits=KEYBOARD_SUBS, relevant_ids=relevant_ids, **llm_kw),
    )


def _keyboard_run(agent: ResearchAgent):
    return agent.synthesise_decision(
        "best mechanical keyboard under $100 SGD", use_cache=False
    )


# ---- 1. the displayed community count cannot disagree with the evidence ----


class _CollapsingLLM(FakeLLM):
    """Embeddings that make the first two threads in the corpus identical.

    Reaches the duplicate-collapse path, where the corpus handed to synthesis
    is SMALLER than the set the filters measured — the one shape where counts
    taken from different stages visibly diverge.
    """

    def embed(self, texts: list[str]) -> list[list[float]]:
        vectors = super().embed(texts)
        if len(vectors) >= 2:
            vectors[1] = list(vectors[0])
        return vectors


def _same_author_corpus() -> list:
    subs = ["SteamDeck", "HandheldPC", "patientgamers", "gaming"]
    return [thread(f"t{i}", subs[i % 4], author="u_one") for i in range(8)]


ALL_SHAPES = {
    "clean multi-community": lambda: _agent(),
    "single community": lambda: _agent(
        reddit=FakeReddit(candidates=[thread(f"s{i}", "SteamDeck") for i in range(6)]),
        llm=FakeLLM(subreddits=["SteamDeck"]),
    ),
    "duplicate collapsed": lambda: _agent(
        reddit=FakeReddit(candidates=_same_author_corpus()), llm=_CollapsingLLM()
    ),
    "relevance screen unavailable": lambda: _agent(llm=FakeLLM(relevance_fails=True)),
    "mixed retrieval": lambda: _keyboard_agent(RELEVANT + OFF_TOPIC),
}


@pytest.mark.parametrize("shape", sorted(ALL_SHAPES))
def test_displayed_facts_agree_with_the_authoritative_evidence(shape):
    """Every count shown must be measured over the threads actually cited.

    This is the invariant the LASIK screen broke: the numbers, the community
    list and the source list are three views of ONE set, so any disagreement
    between them is a bug however plausible each looks alone.
    """
    brief = _run(ALL_SHAPES[shape]())
    sq = brief.signal_quality
    cited = {s.subreddit for s in brief.sources}

    assert sq.usable_thread_count == len(brief.sources), (
        f"{shape}: thread count disagrees with the cited sources"
    )
    assert sq.thread_count == sq.usable_thread_count
    assert set(sq.subreddits_represented) == cited, (
        f"{shape}: communities {sq.subreddits_represented} vs cited {sorted(cited)}"
    )
    assert len(sq.subreddits_represented) == len(cited)
    assert sq.subreddits_represented == sorted(sq.subreddits_represented)
    # A count of threads clearing the quality bar can never exceed the number
    # of threads there are.
    assert sq.strong_thread_count <= sq.usable_thread_count, (
        f"{shape}: more strong threads than threads"
    )


def test_a_collapsed_duplicate_leaves_every_count_consistent():
    """Pins the specific stage where the counts used to come from two sets."""
    brief = _run(
        _agent(reddit=FakeReddit(candidates=_same_author_corpus()), llm=_CollapsingLLM())
    )
    sq = brief.signal_quality
    assert sq.duplicate_threads_collapsed == 1
    assert sq.usable_thread_count == 7
    # Was 8 before the strong count was measured over the final corpus: the
    # collapsed thread had cleared the bar and was still being counted.
    assert sq.strong_thread_count == 7


# ---- 2. a model claim cannot contradict the authoritative communities ------


def test_a_single_community_claim_cannot_contradict_four_communities():
    """The LASIK contradiction, end to end."""
    brief = _run(_agent(llm=FakeLLM(red_flags=[LASIK_RED_FLAG])))

    assert len(brief.signal_quality.subreddits_represented) == 4
    assert LASIK_RED_FLAG not in brief.red_flags
    assert not any("single subreddit" in f.lower() for f in brief.red_flags)
    # Removed, not silently swallowed: the brief records that the synthesis
    # said something its own corpus disproves.
    assert brief.signal_quality.unverified_claims_removed == 1


def test_a_contradicted_claim_in_the_bias_note_is_removed_sentence_by_sentence():
    """The honest half of a mixed paragraph must survive."""
    notes = (
        "Posters skew toward enthusiasts who already own the device. "
        "All evidence comes from a single subreddit, so there is no "
        "cross-community corroboration."
    )
    brief = _run(_agent(llm=FakeLLM(bias_notes=notes)))
    kept = brief.signal_quality.bias_notes

    assert "enthusiasts" in kept, "an honest sentence was thrown away with the wrong one"
    assert "single subreddit" not in kept
    assert brief.signal_quality.unverified_claims_removed == 1


def test_a_true_single_community_claim_is_kept():
    """The check is about consistency, not about suppressing bad news.

    With one community represented the same sentence is TRUE, and removing it
    would hide a real weakness — the opposite of the bug being fixed.
    """
    corpus = [thread(f"s{i}", "lasik") for i in range(6)]
    brief = _run(
        _agent(
            reddit=FakeReddit(candidates=corpus),
            llm=FakeLLM(subreddits=["lasik"], red_flags=[LASIK_RED_FLAG]),
        )
    )
    assert brief.signal_quality.subreddits_represented == ["lasik"]
    assert LASIK_RED_FLAG in brief.red_flags
    assert brief.signal_quality.unverified_claims_removed == 0


@pytest.mark.parametrize(
    "claim",
    [
        "All evidence from a single subreddit (r/lasik) — no cross-community corroboration",
        "Everything here comes from one subreddit.",
        "This is a single-subreddit sample.",
        "There is no cross-subreddit corroboration for the recommendation.",
        "All of the discussion is from r/lasik.",
        "Only one community is represented in this evidence.",
    ],
)
def test_single_community_phrasings_are_all_caught(claim):
    multi = UsableEvidence.of([thread("a", "one"), thread("b", "two")])
    assert contradicts(claim, multi), f"unchecked structural claim: {claim!r}"


@pytest.mark.parametrize(
    "claim",
    [
        "One commenter recommends waiting for the next revision.",
        "The vendor deleted negative reviews from its own community.",
        "Praise appears across multiple subreddits with unusual polish.",
        "Evidence spans more than one subreddit but the threads are old.",
        "Several communities report the same firmware fault.",
        "",
    ],
)
def test_ordinary_claims_are_never_touched(claim):
    """A consistency check, not a censor: what it cannot disprove, it keeps."""
    multi = UsableEvidence.of([thread("a", "one"), thread("b", "two")])
    assert not contradicts(claim, multi)
    kept, removed = verified_claims([claim], multi)
    assert kept == [claim] and removed == 0


def test_a_bare_string_is_one_claim_not_a_pile_of_letters():
    """A model can answer with a string where the schema asks for a list."""
    multi = UsableEvidence.of([thread("a", "one"), thread("b", "two")])
    assert verified_claims("Vendor support is slow", multi) == (
        ["Vendor support is slow"],
        0,
    )
    assert verified_claims(LASIK_RED_FLAG, multi) == ([], 1)


# ---- 3 & 4. rejected threads cannot inflate anything ----------------------


def test_irrelevant_threads_cannot_increase_the_represented_community_count():
    """r/singapore threads about laptops are not keyboard communities."""
    brief = _keyboard_run(_keyboard_agent(RELEVANT + OFF_TOPIC))
    represented = brief.signal_quality.subreddits_represented

    assert represented == ["MechanicalKeyboards", "buildapc"]
    assert "singapore" not in represented
    assert "askSingapore" not in represented
    assert brief.signal_quality.off_topic_candidates_rejected == len(OFF_TOPIC)


def test_irrelevant_threads_cannot_increase_the_usable_thread_count():
    """Adding off-topic retrieval must change nothing the confidence sees."""
    clean = _keyboard_run(_keyboard_agent(RELEVANT))
    polluted = _keyboard_run(_keyboard_agent(RELEVANT + OFF_TOPIC))

    assert clean.signal_quality.usable_thread_count == len(RELEVANT)
    assert (
        polluted.signal_quality.usable_thread_count
        == clean.signal_quality.usable_thread_count
    )
    assert (
        polluted.signal_quality.subreddits_represented
        == clean.signal_quality.subreddits_represented
    )
    assert polluted.structural_ceiling is clean.structural_ceiling
    assert polluted.confidence is clean.confidence
    assert {s.id for s in polluted.sources} == set(RELEVANT_IDS)


def test_off_topic_threads_cannot_lift_a_narrow_corpus_to_high():
    """The concrete danger: four off-topic subs would have satisfied rule 3.

    Two real communities cannot earn HIGH. Two real communities plus two
    irrelevant ones must not either — which is exactly what used to happen,
    because the count could not tell them apart.
    """
    polluted = _keyboard_run(_keyboard_agent(RELEVANT + OFF_TOPIC))
    assert polluted.semantic_confidence is HIGH, "the model was willing to say HIGH"
    assert polluted.structural_ceiling is MODERATE
    assert polluted.confidence is MODERATE


# ---- 5. the keyboard query stays LOW when the real evidence is thin -------


def test_keyboard_style_mixed_retrieval_stays_low():
    """One real hit buried in popular off-topic threads is still one hit."""
    agent = _keyboard_agent(RELEVANT[:1] + OFF_TOPIC, relevant_ids=["kb1"])
    brief = _keyboard_run(agent)

    assert brief.signal_quality.usable_thread_count == 1
    assert brief.signal_quality.subreddits_represented == ["MechanicalKeyboards"]
    assert brief.confidence is LOW
    assert any("one usable thread" in r.lower() for r in brief.confidence_reasons)


def test_a_corpus_of_only_off_topic_threads_produces_no_verdict():
    """Retrieval found things; none of them were about the question."""
    agent = _keyboard_agent(OFF_TOPIC, relevant_ids=[])
    brief = _keyboard_run(agent)

    assert brief.signal_quality.evidence_state is EvidenceState.NO_EVIDENCE
    assert brief.confidence is LOW
    assert brief.signal_quality.usable_thread_count == 0
    assert brief.signal_quality.subreddits_represented == []
    assert brief.sources == []


# ---- 6. valid multi-community results still work -------------------------


def test_a_valid_multi_community_result_still_reaches_high():
    """If this fails, the fix has bought consistency by refusing everything."""
    brief = _run(_agent())
    assert brief.semantic_confidence is HIGH
    assert brief.structural_ceiling is HIGH
    assert brief.confidence is HIGH
    assert brief.confidence_reasons == []
    assert brief.signal_quality.usable_thread_count == 8
    assert len(brief.signal_quality.subreddits_represented) == 4
    assert brief.signal_quality.relevance_screened is True
    assert brief.signal_quality.unverified_claims_removed == 0


def test_an_honest_red_flag_survives_a_multi_community_corpus():
    """Verification must not quietly strip ordinary warnings."""
    flags = ["Vendor support is slow", "Praise clusters in low-score comments"]
    brief = _run(_agent(llm=FakeLLM(red_flags=flags)))
    assert brief.red_flags == flags
    assert brief.signal_quality.unverified_claims_removed == 0


# ---- 7. no evidence stays LOW --------------------------------------------


@pytest.mark.parametrize(
    "agent_factory, expected_state",
    [
        (
            lambda: _agent(reddit=FakeReddit(candidates=[], live_ok=True)),
            EvidenceState.NO_EVIDENCE,
        ),
        (
            lambda: _agent(reddit=FakeReddit(candidates=[], live_ok=False)),
            EvidenceState.SOURCE_UNAVAILABLE,
        ),
    ],
)
def test_no_evidence_paths_remain_low(agent_factory, expected_state):
    brief = _run(agent_factory())
    assert brief.confidence is LOW
    assert brief.signal_quality.evidence_state is expected_state
    assert brief.signal_quality.usable_thread_count == 0
    assert brief.signal_quality.subreddits_represented == []
    assert brief.confidence_reasons, "an empty brief must still explain itself"


# ---- 8. the new machinery can only ever lower a verdict ------------------


def test_relevance_screening_and_verification_never_raise_confidence():
    """Neither new stage may promote a verdict, on any shape.

    The model still owns the ceiling of what may be claimed; everything added
    here is allowed to subtract from it and nothing else.
    """
    shapes = {
        "screened, all relevant": _agent(),
        "screen unavailable": _agent(llm=FakeLLM(relevance_fails=True)),
        "screen answered about other posts": _agent(
            llm=FakeLLM(relevance_reply={"relevant_ids": ["not-a-candidate"]})
        ),
        "screen returned no verdict": _agent(llm=FakeLLM(relevance_reply={})),
        "contradicted claim removed": _agent(llm=FakeLLM(red_flags=[LASIK_RED_FLAG])),
        "mixed retrieval": _keyboard_agent(RELEVANT + OFF_TOPIC),
    }
    for name, agent in shapes.items():
        brief = _run(agent)
        assert RANK[brief.confidence] <= RANK[brief.semantic_confidence], (
            f"{name}: final outranked the model's own judgement"
        )
        assert RANK[brief.confidence] <= RANK[brief.structural_ceiling], (
            f"{name}: final outranked the structural ceiling"
        )


def test_an_unusable_screen_answer_keeps_the_corpus_but_lowers_the_ceiling():
    """Failing open on retrieval, failing closed on confidence.

    An LLM outage must not empty a corpus — but an unscreened corpus may
    contain threads nobody checked were about the question, so it cannot back
    a HIGH claim either.
    """
    for llm in (
        FakeLLM(relevance_fails=True),
        FakeLLM(relevance_reply={}),
        FakeLLM(relevance_reply={"relevant_ids": ["not-a-candidate"]}),
    ):
        brief = _run(_agent(llm=llm))
        assert brief.signal_quality.usable_thread_count == len(strong_corpus())
        assert brief.signal_quality.relevance_screened is False
        assert brief.structural_ceiling is MODERATE
        assert brief.confidence is MODERATE
        assert any("relevance" in r.lower() for r in brief.confidence_reasons)


def test_an_explicit_empty_verdict_is_respected_not_treated_as_a_failure():
    """"Nothing here is relevant" is an answer; "I could not answer" is not."""
    empty = _keyboard_run(_keyboard_agent(OFF_TOPIC, relevant_ids=[]))
    assert empty.signal_quality.evidence_state is EvidenceState.NO_EVIDENCE

    unusable = _run(_agent(llm=FakeLLM(relevance_reply={})))
    assert unusable.signal_quality.evidence_state is EvidenceState.OK
    assert unusable.signal_quality.usable_thread_count > 0
