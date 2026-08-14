"""Why a brief is empty is not the same question as how confident it is.

"The community disagrees", "we found nothing", "Reddit is blocked" and "that
question isn't in the frozen corpus" used to arrive as one indistinguishable
`confidence: low`, with the difference buried in prose. These tests pin each
runtime branch to its own machine-readable state, and prove the structural
confidence policy is actually reached by the real pipeline rather than only by
its own unit tests.

Everything is driven through fakes: no network, no OpenRouter, no Reddit, no
Supabase, no fixture corpus on disk.
"""
from __future__ import annotations

import pytest

from agents.research_agent import NoRecordedPlan, ResearchAgent
from core.models import ConfidenceLevel, EvidenceState, RedditThread

HIGH = ConfidenceLevel.HIGH
MODERATE = ConfidenceLevel.MODERATE
LOW = ConfidenceLevel.LOW


def _thread(tid: str, sub: str, score: int = 100, comments: int = 40) -> RedditThread:
    """A thread that clears the strong-evidence bar (score >= 10, comments >= 3)."""
    return RedditThread(
        id=tid,
        subreddit=sub,
        title=f"thread {tid}",
        body=f"body of {tid}",
        url=f"https://old.reddit.com/comments/{tid}/",
        score=score,
        num_comments=comments,
        created_utc=1_700_000_000.0,
        author=f"u_{tid}",
        top_comments=[f"[{score} pts] a comment on {tid}"],
    )


def _strong_corpus() -> list[RedditThread]:
    """8 threads across 4 communities — nothing here should cap confidence."""
    subs = ["SteamDeck", "HandheldPC", "patientgamers", "gaming"]
    return [_thread(f"t{i}", subs[i % 4]) for i in range(8)]


class _FakeReddit:
    """Stands in for RedditService at the seams the agent actually uses."""

    def __init__(
        self,
        candidates: list[RedditThread] | None = None,
        threads: list[RedditThread] | None = None,
        live_ok: bool = True,
    ):
        self._candidates = candidates if candidates is not None else _strong_corpus()
        self._threads = {t.id: t for t in (threads if threads is not None else self._candidates)}
        self._live_ok = live_ok

    def search_subreddit(self, sub, query, time_filter="year", limit=25):
        return [t for t in self._candidates if t.subreddit == sub]

    def get_thread_with_comments(self, thread_id, max_comments=15):
        return self._threads[thread_id]

    def probe_live(self):
        return (self._live_ok, "reachable" if self._live_ok else "HTTP 403 — blocked")

    def apply_signal_filters(self, threads, min_score=10, min_comments=3):
        # The real implementation — imported rather than reimplemented, so
        # these tests exercise the genuine relaxation decision.
        from services.reddit import RedditService

        return RedditService.apply_signal_filters(self, threads, min_score, min_comments)


class _FetchAlwaysFails(_FakeReddit):
    """Search returns candidates; every full-thread fetch dies.

    The real production route to an empty synthesis corpus: `_fetch_threads`
    drops failures with a warning, so the corpus arrives empty even though
    search found plenty.
    """

    def get_thread_with_comments(self, thread_id, max_comments=15):
        raise RuntimeError("thread fetch failed")


class _FakeLLM:
    """Scripted planner and synthesis. Embeddings succeed unless told not to."""

    def __init__(
        self,
        subreddits: list[str] | None = None,
        confidence: str = "high",
        consensus_pick: str = "The Steam Deck OLED, for its screen and battery.",
        embed_fails: bool = False,
        drop_half: str | None = None,
    ):
        self._subreddits = subreddits or ["SteamDeck", "HandheldPC", "patientgamers", "gaming"]
        self._confidence = confidence
        self._pick = consensus_pick
        self._embed_fails = embed_fails
        self._drop_half = drop_half
        self.worst_case_seconds = 1

    def complete_json(self, system: str, user: str, max_tokens: int = 0, **kw) -> dict:
        if "identify which subreddits" in system:
            return {"subreddits": self._subreddits, "search_queries": ["q1", "q2"]}
        if "audit Reddit discussions" in system:          # scrutiny half
            if self._drop_half == "scrutiny":
                raise RuntimeError("scrutiny half unavailable")
            return {
                "failure_modes": ["fan noise"],
                "what_reviewers_miss": ["case fit"],
                "red_flags": [],
                "confidence": self._confidence,
                "bias_notes": "enthusiast-skewed sample",
            }
        if self._drop_half == "verdict":                   # verdict half
            raise RuntimeError("verdict half unavailable")
        return {
            "consensus_pick": self._pick,
            "strengths": ["screen"],
            "alternatives": ["ROG Ally X"],
        }

    def embed(self, texts: list[str]) -> list[list[float]]:
        if self._embed_fails:
            raise RuntimeError("embeddings unavailable")
        # Mutually distant unit vectors: no pair reaches the 0.90 threshold.
        return [[1.0 if i == j else 0.0 for j in range(len(texts))] for i in range(len(texts))]


def _agent(reddit=None, llm=None) -> ResearchAgent:
    return ResearchAgent(reddit=reddit or _FakeReddit(), llm=llm or _FakeLLM())


def _run(agent: ResearchAgent, query: str = "is the Steam Deck OLED worth it"):
    # use_cache=False everywhere: these tests are about the pipeline, and a
    # cache hit would bypass exactly what they exist to check.
    return agent.synthesise_decision(query, use_cache=False)


# ---- the five states -----------------------------------------------------


def test_case20_a_normal_synthesis_is_ok():
    brief = _run(_agent())
    assert brief.signal_quality.evidence_state is EvidenceState.OK


def test_case17_no_candidates_with_a_reachable_source_is_no_evidence():
    brief = _run(_agent(reddit=_FakeReddit(candidates=[], live_ok=True)))
    assert brief.signal_quality.evidence_state is EvidenceState.NO_EVIDENCE
    assert brief.confidence is LOW


def test_case18_no_candidates_with_a_dead_source_is_source_unavailable():
    """An upstream outage must never read as a verdict on the question."""
    brief = _run(_agent(reddit=_FakeReddit(candidates=[], live_ok=False)))
    assert brief.signal_quality.evidence_state is EvidenceState.SOURCE_UNAVAILABLE
    assert brief.confidence is LOW


def test_case16_an_unrecorded_query_is_not_in_corpus(monkeypatch):
    agent = _agent()

    def _no_plan(_query):
        raise NoRecordedPlan("not in the recorded corpus")

    monkeypatch.setattr(agent, "_identify_subreddits", _no_plan)
    brief = _run(agent, "a question nobody captured")
    assert brief.signal_quality.evidence_state is EvidenceState.NOT_IN_CORPUS
    assert brief.confidence is LOW


def test_case19_candidates_that_cannot_be_fetched_are_no_usable_evidence():
    """The genuinely reachable shape: search hits exist, every full fetch fails.

    NOT "the filters rejected everything" — `apply_signal_filters` relaxes
    until something survives and returns empty only for empty input, so an
    empty corpus at this point means retrieval produced nothing.
    """
    brief = _run(_agent(reddit=_FetchAlwaysFails()))
    assert brief.signal_quality.evidence_state is EvidenceState.NO_USABLE_EVIDENCE
    assert brief.confidence is LOW
    # The explanation must not blame a stage that never rejected anything.
    note = brief.signal_quality.bias_notes.lower()
    assert "filter" not in note, f"blamed filtering for a retrieval failure: {note!r}"
    assert "retriev" in note or "full thread" in note


def test_filters_cannot_empty_a_non_empty_corpus():
    """Pins the property the wording above depends on.

    If a future change lets filtering return nothing for a non-empty input,
    the NO_USABLE_EVIDENCE copy becomes wrong and this test says so.
    """
    from services.reddit import RedditService

    svc = object.__new__(RedditService)  # no __init__: no client, no network
    for shape in [(0, 0), (0, 5), (5, 0), (2, 1), (50, 20)]:
        threads = [_thread(f"x{i}", "s", *shape) for i in range(3)]
        assert RedditService.apply_signal_filters(svc, threads).threads, (
            f"filtering emptied a non-empty corpus for {shape}"
        )
    assert RedditService.apply_signal_filters(svc, []).threads == []


def test_the_four_empty_states_are_mutually_distinct(monkeypatch):
    """The whole point: these must not collapse back into one value.

    All four are obtained by driving the real pipeline branches, not by
    naming the enum members.
    """
    no_evidence = _run(_agent(reddit=_FakeReddit(candidates=[], live_ok=True)))
    source_down = _run(_agent(reddit=_FakeReddit(candidates=[], live_ok=False)))
    unfetchable = _run(_agent(reddit=_FetchAlwaysFails()))

    off_corpus_agent = _agent()

    def _no_plan(_query):
        raise NoRecordedPlan("not in the recorded corpus")

    monkeypatch.setattr(off_corpus_agent, "_identify_subreddits", _no_plan)
    off_corpus = _run(off_corpus_agent, "a question nobody captured")

    states = {
        b.signal_quality.evidence_state
        for b in (no_evidence, source_down, unfetchable, off_corpus)
    }
    assert states == {
        EvidenceState.NO_EVIDENCE,
        EvidenceState.SOURCE_UNAVAILABLE,
        EvidenceState.NO_USABLE_EVIDENCE,
        EvidenceState.NOT_IN_CORPUS,
    }, f"the four empty states did not stay distinct: {states}"
    # All four are LOW — the confidence value alone cannot tell them apart,
    # which is exactly why the state field has to exist.
    assert all(
        b.confidence is LOW for b in (no_evidence, source_down, unfetchable, off_corpus)
    )


# ---- each empty state explains its OWN branch -----------------------------


def _reason_for(brief) -> str:
    assert brief.confidence_reasons, "an empty brief must still explain itself"
    return " ".join(brief.confidence_reasons).lower()


def test_the_off_corpus_reason_does_not_mention_filtering_or_retrieval(monkeypatch):
    """Nothing was retrieved or filtered — there was no plan to act on."""
    agent = _agent()

    def _no_plan(_query):
        raise NoRecordedPlan("not in the recorded corpus")

    monkeypatch.setattr(agent, "_identify_subreddits", _no_plan)
    reason = _reason_for(_run(agent, "a question nobody captured"))

    assert "corpus" in reason
    assert "filter" not in reason
    assert "retriev" not in reason
    assert "survived" not in reason


def test_the_source_unavailable_reason_does_not_blame_filtering():
    reason = _reason_for(_run(_agent(reddit=_FakeReddit(candidates=[], live_ok=False))))
    assert "unavailable" in reason
    assert "filter" not in reason
    assert "survived" not in reason


def test_the_no_evidence_reason_does_not_claim_threads_were_filtered():
    """Search was reachable and returned nothing — no thread ever existed."""
    reason = _reason_for(_run(_agent(reddit=_FakeReddit(candidates=[], live_ok=True))))
    assert "no relevant community threads" in reason
    assert "filter" not in reason
    assert "survived" not in reason


def test_the_unfetchable_reason_names_retrieval_not_filtering():
    reason = _reason_for(_run(_agent(reddit=_FetchAlwaysFails())))
    assert "candidate" in reason
    assert "retriev" in reason
    assert "filter" not in reason


def test_the_four_empty_states_give_four_different_explanations(monkeypatch):
    """One generic sentence for all four was wrong in three of them."""
    agent = _agent()

    def _no_plan(_query):
        raise NoRecordedPlan("not in the recorded corpus")

    monkeypatch.setattr(agent, "_identify_subreddits", _no_plan)

    reasons = {
        _reason_for(_run(_agent(reddit=_FakeReddit(candidates=[], live_ok=True)))),
        _reason_for(_run(_agent(reddit=_FakeReddit(candidates=[], live_ok=False)))),
        _reason_for(_run(_agent(reddit=_FetchAlwaysFails()))),
        _reason_for(_run(agent, "a question nobody captured")),
    }
    assert len(reasons) == 4, f"empty states share an explanation: {reasons}"


def test_a_low_verdict_with_state_ok_is_a_real_research_result():
    """Genuine disagreement must be distinguishable from a source failure."""
    brief = _run(_agent(llm=_FakeLLM(confidence="low")))
    assert brief.confidence is LOW
    assert brief.signal_quality.evidence_state is EvidenceState.OK


# ---- the policy is genuinely reached by the real pipeline ----------------


def test_strong_evidence_and_a_confident_model_survive_the_pipeline():
    """If this fails, every cap below is meaningless — nothing could be HIGH."""
    brief = _run(_agent())
    assert brief.semantic_confidence is HIGH
    assert brief.structural_ceiling is HIGH
    assert brief.confidence is HIGH
    assert brief.confidence_reasons == []


def test_a_single_community_caps_the_real_pipeline_at_moderate():
    corpus = [_thread(f"s{i}", "SteamDeck") for i in range(6)]
    agent = _agent(
        reddit=_FakeReddit(candidates=corpus),
        llm=_FakeLLM(subreddits=["SteamDeck"]),
    )
    brief = _run(agent)
    assert brief.semantic_confidence is HIGH
    assert brief.structural_ceiling is MODERATE
    assert brief.confidence is MODERATE
    assert any("communities" in r or "community" in r for r in brief.confidence_reasons)


def test_relaxed_filters_cap_the_real_pipeline_at_moderate():
    """Four weak threads across four subs: the strong bar is never met."""
    subs = ["SteamDeck", "HandheldPC", "patientgamers", "gaming"]
    weak = [_thread(f"w{i}", subs[i], score=2, comments=1) for i in range(4)]
    brief = _run(_agent(reddit=_FakeReddit(candidates=weak)))
    assert brief.signal_quality.filters_relaxed is True
    assert brief.structural_ceiling is MODERATE
    assert brief.confidence is MODERATE
    assert any("engagement quality bar" in r for r in brief.confidence_reasons)
    # Nothing weaker was admitted here either — all four threads are the ones
    # that were fetched. The copy must not imply otherwise.
    assert not any("weaker" in r.lower() for r in brief.confidence_reasons)


def test_an_embeddings_outage_caps_the_real_pipeline_at_moderate():
    brief = _run(_agent(llm=_FakeLLM(embed_fails=True)))
    assert brief.signal_quality.similarity_analysis_available is False
    assert brief.structural_ceiling is MODERATE
    assert brief.confidence is MODERATE
    assert any("Similarity analysis" in r for r in brief.confidence_reasons)


def test_case11_a_failed_verdict_half_cannot_leave_a_confident_brief():
    """Scrutiny returns HIGH, but there is no pick to be confident about."""
    brief = _run(_agent(llm=_FakeLLM(drop_half="verdict", consensus_pick="")))
    assert brief.consensus_pick == ""
    assert brief.confidence is LOW


def test_a_failed_scrutiny_half_falls_back_to_low_not_to_a_guess():
    brief = _run(_agent(llm=_FakeLLM(drop_half="scrutiny")))
    assert brief.semantic_confidence is LOW
    assert brief.confidence is LOW


# ---- provenance ----------------------------------------------------------


def test_represented_communities_are_the_ones_that_actually_contributed():
    """The planner names four; only one yields threads."""
    corpus = [_thread(f"s{i}", "SteamDeck") for i in range(6)]
    agent = _agent(
        reddit=_FakeReddit(candidates=corpus),
        llm=_FakeLLM(subreddits=["SteamDeck", "HandheldPC", "gaming", "patientgamers"]),
    )
    brief = _run(agent)
    assert brief.signal_quality.subreddits_represented == ["SteamDeck"]
    assert len(brief.signal_quality.subreddits_checked) == 4
    # The claim the UI makes must match the sources it shows.
    assert {s.subreddit for s in brief.sources} == set(
        brief.signal_quality.subreddits_represented
    )


def test_represented_communities_are_deterministically_ordered():
    assert _run(_agent()).signal_quality.subreddits_represented == sorted(
        _run(_agent()).signal_quality.subreddits_represented
    )


def test_thread_count_alias_matches_the_canonical_field():
    sq = _run(_agent()).signal_quality
    assert sq.thread_count == sq.usable_thread_count


def test_strong_thread_count_is_reported_even_when_filters_did_not_relax():
    sq = _run(_agent()).signal_quality
    assert sq.strong_thread_count == 8
    assert sq.filters_relaxed is False


# ---- fail-closed defaults ------------------------------------------------


def test_a_brief_built_with_no_arguments_denies_rather_than_claims():
    """The previous MODERATE default let an unspecified brief look decided."""
    from core.models import ResearchBrief

    brief = ResearchBrief()
    assert brief.confidence is LOW
    assert brief.semantic_confidence is LOW
    assert brief.structural_ceiling is LOW


def test_an_empty_pick_forces_low_at_model_level_even_with_a_clean_corpus():
    """CASE 1: both layers said HIGH; the no-pick rule overrides them.

    The pipeline already enforces this, but the guarantee should hold for any
    brief that can be CONSTRUCTED — including one deserialised from disk.
    """
    from core.models import ResearchBrief

    brief = ResearchBrief(
        consensus_pick="",
        confidence=HIGH,
        semantic_confidence=HIGH,
        structural_ceiling=HIGH,
    )
    assert brief.confidence is LOW
    # The record of what each layer decided must survive intact — it is the
    # only way the UI can explain that something else caused the downgrade.
    assert brief.semantic_confidence is HIGH
    assert brief.structural_ceiling is HIGH


def test_an_empty_pick_forces_low_under_a_partial_cap_too():
    """CASE 2: semantic HIGH, ceiling MODERATE, no pick -> LOW, not MODERATE."""
    from core.models import ResearchBrief

    brief = ResearchBrief(
        consensus_pick="",
        confidence=MODERATE,
        semantic_confidence=HIGH,
        structural_ceiling=MODERATE,
    )
    assert brief.confidence is LOW
    assert brief.semantic_confidence is HIGH
    assert brief.structural_ceiling is MODERATE


@pytest.mark.parametrize(
    "state",
    [
        EvidenceState.SOURCE_UNAVAILABLE,
        EvidenceState.NOT_IN_CORPUS,
        EvidenceState.NO_EVIDENCE,
        EvidenceState.NO_USABLE_EVIDENCE,
    ],
)
def test_a_non_ok_evidence_state_forces_low_at_model_level(state):
    """No evidence cannot authorise a verdict, however confident the fields.

    Everything else about this brief looks strong — a real pick, both layers
    HIGH — which is precisely the shape a stale cache entry or a hand-built
    object could take.
    """
    from core.models import ResearchBrief, SignalQuality

    brief = ResearchBrief(
        consensus_pick="The Keychron K2 HE.",
        confidence=HIGH,
        semantic_confidence=HIGH,
        structural_ceiling=HIGH,
        signal_quality=SignalQuality(evidence_state=state),
    )
    assert brief.confidence is LOW
    # The record of what each layer decided is preserved, not rewritten.
    assert brief.semantic_confidence is HIGH
    assert brief.structural_ceiling is HIGH


def test_state_ok_leaves_a_legitimate_high_brief_alone():
    """The guard above must not touch the state every real verdict has."""
    from core.models import ResearchBrief, SignalQuality

    brief = ResearchBrief(
        consensus_pick="The Keychron K2 HE.",
        confidence=HIGH,
        semantic_confidence=HIGH,
        structural_ceiling=HIGH,
        signal_quality=SignalQuality(evidence_state=EvidenceState.OK),
    )
    assert brief.confidence is HIGH


def test_a_whitespace_only_pick_is_not_a_pick():
    from core.models import ResearchBrief

    brief = ResearchBrief(
        consensus_pick="   \n ",
        confidence=HIGH,
        semantic_confidence=HIGH,
        structural_ceiling=HIGH,
    )
    assert brief.confidence is LOW


def test_a_brief_cannot_represent_a_verdict_above_its_own_ceiling():
    """The invariant holds for anything CONSTRUCTIBLE, not just what the
    pipeline happens to build — including a payload from an older schema."""
    from core.models import ResearchBrief

    forged = ResearchBrief.model_validate(
        {"consensus_pick": "x", "confidence": "high"}  # no ceiling, no semantic
    )
    assert forged.confidence is LOW

    explicit = ResearchBrief(
        consensus_pick="x",
        confidence=HIGH,
        semantic_confidence=HIGH,
        structural_ceiling=MODERATE,
    )
    assert explicit.confidence is MODERATE, "final outranked the structural ceiling"

    cautious = ResearchBrief(
        consensus_pick="x",
        confidence=HIGH,
        semantic_confidence=LOW,
        structural_ceiling=HIGH,
    )
    assert cautious.confidence is LOW, "final outranked the model's own judgement"


def test_the_clamp_leaves_legitimate_briefs_alone():
    """It must be a no-op on every shape the pipeline actually produces."""
    from core.models import ResearchBrief

    for final, semantic, ceiling in [
        (HIGH, HIGH, HIGH),
        (MODERATE, HIGH, MODERATE),
        (MODERATE, MODERATE, HIGH),
        (LOW, LOW, LOW),
        (LOW, LOW, HIGH),
    ]:
        brief = ResearchBrief(
            consensus_pick="x",
            confidence=final,
            semantic_confidence=semantic,
            structural_ceiling=ceiling,
        )
        assert brief.confidence is final, f"clamp altered a valid {final} brief"


def test_a_capped_brief_survives_the_cache_round_trip():
    """Serialise/deserialise must not re-clamp or lose the calibration."""
    from core.models import ResearchBrief

    original = ResearchBrief(
        consensus_pick="x",
        confidence=MODERATE,
        semantic_confidence=HIGH,
        structural_ceiling=MODERATE,
        confidence_reasons=["Evidence is represented in 2 communities; ..."],
    )
    restored = ResearchBrief.model_validate(original.model_dump(mode="json"))
    assert restored.confidence is MODERATE
    assert restored.semantic_confidence is HIGH
    assert restored.structural_ceiling is MODERATE
    assert restored.confidence_reasons == original.confidence_reasons


@pytest.mark.parametrize(
    "state",
    [
        EvidenceState.OK,
        EvidenceState.NO_EVIDENCE,
        EvidenceState.NO_USABLE_EVIDENCE,
        EvidenceState.SOURCE_UNAVAILABLE,
        EvidenceState.NOT_IN_CORPUS,
    ],
)
def test_every_state_survives_json_serialisation(state):
    """The frontend reads these as string literals over the wire."""
    from core.models import ResearchBrief, SignalQuality

    brief = ResearchBrief(signal_quality=SignalQuality(evidence_state=state))
    assert brief.model_dump(mode="json")["signal_quality"]["evidence_state"] == state.value
