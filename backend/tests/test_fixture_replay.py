"""The recorded corpus and the thread selector must stay in agreement.

THE BUG THESE PIN. `capture_corpus` records the exact requests the research
path makes, so a frozen corpus holds every search page plus the full text of
whichever threads the selector chose AT CAPTURE TIME — and nothing else. When
the relevance ranking changed which threads get chosen, the new picks were
threads whose search-page entry existed but whose body had never been
downloaded. Every one raised FixtureMissing, `_fetch_threads` dropped it, and
eight selections became one surviving thread. Confidence then correctly
reported LOW, so nothing looked broken from the outside.

Nothing here asserts a confidence label. A ranking change must not be able to
quietly empty the corpus; that is a different question from whether the
evidence is any good, and tying the two together is how a test starts pushing
for a flattering answer.
"""

from __future__ import annotations

import pytest

from agents import relevance
from agents.research_agent import (
    _MAX_SUBREDDITS,
    _SEARCH_LIMIT_PER_SUB,
    ResearchAgent,
    _spread_pick,
    _unreadable_note,
)
from core.models import RedditThread
from services import reddit_fixtures as fx
from services.reddit import RedditService

ANC = "best noise cancelling headphones for open office"
STEAM_DECK = "is the Steam Deck OLED worth it in 2026"
KEYBOARD = "best budget mechanical keyboard for programming under $100"

# What each recorded query must still deliver. The keyboard query is the
# deliberately-weak demo beat — thin evidence is the point, no evidence is a
# broken corpus.
RECORDED = [(ANC, 5), (STEAM_DECK, 5), (KEYBOARD, 3)]


@pytest.fixture
def replay(monkeypatch):
    """A RedditService pinned to replay-only, with settings cleared so the mode
    actually takes effect."""
    from core.config import get_settings

    monkeypatch.setenv("REDDIT_SOURCE", "fixture")
    get_settings.cache_clear()
    yield RedditService()
    get_settings.cache_clear()


def collect(reddit: RedditService, query: str):
    """The search fan-out `synthesise_decision` performs, and its plan."""
    plan = fx.load_plan(query)
    assert plan is not None, f"no recorded plan for {query!r}"
    subs = plan["subreddits"][:_MAX_SUBREDDITS]
    phrases = plan["search_queries"]
    found: list[RedditThread] = []
    for sub in subs:
        for phrase in phrases:
            found.extend(
                reddit.search_subreddit(
                    sub, phrase, time_filter="year", limit=_SEARCH_LIMIT_PER_SUB
                )
            )
    seen: set[str] = set()
    return [t for t in found if not (t.id in seen or seen.add(t.id))], phrases


def select(agent: ResearchAgent, query: str, candidates, phrases, budget: int = 8):
    """Selection exactly as the agent does it: narrow, weigh, pick."""
    pool = agent._servable(candidates)
    weights = relevance.build_weights(query, phrases, pool)
    return _spread_pick(pool, budget, weights), pool, weights


def thread(tid: str, sub: str = "s", title: str = "t", score: int = 0, comments: int = 0):
    return RedditThread(
        id=tid, subreddit=sub, title=title, url=f"https://reddit.com/{tid}",
        score=score, num_comments=comments,
    )


# ---- the invariant that would have caught this ----

@pytest.mark.parametrize("query,_floor", RECORDED)
def test_replay_never_selects_a_thread_the_corpus_cannot_serve(query, _floor, replay):
    """THE regression guard. Whatever the ranking does, every selected thread
    must have a recorded full thread — otherwise the fetch cannot succeed and
    the slot is wasted. This fails on any future ranking change that diverges
    from the corpus, without needing to know what changed."""
    agent = ResearchAgent(reddit=replay, llm=object())
    candidates, phrases = collect(replay, query)
    chosen, _, _ = select(agent, query, candidates, phrases)

    assert chosen, "selection returned nothing"
    missing = [t.id for t in chosen if not replay.has_recorded_thread(t.id)]
    assert not missing, (
        f"{len(missing)} selected threads have no recorded full thread: {missing}. "
        "Selection has drifted from the captured corpus — recapture, or narrow "
        "the pool before ranking."
    )


@pytest.mark.parametrize("query,floor", RECORDED)
def test_recorded_queries_still_reach_synthesis_with_usable_evidence(query, floor, replay):
    """The demo queries must still deliver real threads to synthesis. Measured
    at the same seam the agent uses: fetch, then signal-filter."""
    agent = ResearchAgent(reddit=replay, llm=object())
    candidates, phrases = collect(replay, query)
    chosen, _, _ = select(agent, query, candidates, phrases)

    fetched = agent._fetch_threads(chosen)
    surviving = replay.apply_signal_filters(fetched)

    assert len(fetched) == len(chosen), "a selected thread failed to fetch on replay"
    assert len(surviving) >= floor, (
        f"{query!r} delivered {len(surviving)} threads to synthesis, below {floor}"
    )


def test_the_weak_demo_query_stays_honestly_weak(replay):
    """The keyboard query is the "an agent that admits it does not know" beat.
    It must keep working AND keep being thin — if a change ever made its
    evidence strong, that is a signal something is being inflated."""
    agent = ResearchAgent(reddit=replay, llm=object())
    candidates, phrases = collect(replay, KEYBOARD)
    chosen, pool, weights = select(agent, KEYBOARD, candidates, phrases)

    scores = sorted((relevance.score(t, weights) for t in chosen), reverse=True)
    assert scores[0] > 0, "not one selected thread mentions the topic at all"
    # Most of what the corpus has for this query is switch-sale roundups, a
    # giveaway and a store post — tangential by construction.
    assert sum(1 for s in scores if s == 0) >= 2, (
        "this query's corpus is expected to be largely tangential; if it is now "
        "strongly on-topic, check what changed before trusting it"
    )


# ---- relevance ranking still runs, it is not bypassed ----

@pytest.mark.parametrize("query,_floor", RECORDED)
def test_relevance_still_orders_the_available_candidates(query, _floor, replay):
    """Narrowing must not turn selection back into the old engagement-first
    behaviour. Within a subreddit, the more relevant thread must come first."""
    agent = ResearchAgent(reddit=replay, llm=object())
    candidates, phrases = collect(replay, query)
    chosen, _, weights = select(agent, query, candidates, phrases)

    by_sub: dict[str, list[float]] = {}
    for t in chosen:
        by_sub.setdefault(t.subreddit, []).append(relevance.score(t, weights))
    ordered = [s for s in by_sub.values() if len(s) > 1]
    assert ordered, "expected at least one subreddit to contribute two threads"
    for scores in ordered:
        assert scores == sorted(scores, reverse=True), (
            "relevance order was lost inside a subreddit — narrowing must not "
            "bypass the ranking"
        )


def test_narrowing_picks_the_most_relevant_of_what_is_available(replay):
    """The point of Option C: rank WITHIN the servable pool rather than
    reverting to engagement. The top pick must be the highest-scoring
    available candidate, not the highest-engagement one."""
    agent = ResearchAgent(reddit=replay, llm=object())
    candidates, phrases = collect(replay, STEAM_DECK)
    chosen, pool, weights = select(agent, STEAM_DECK, candidates, phrases)

    best_available = max(relevance.score(t, weights) for t in pool)
    assert relevance.score(chosen[0], weights) == best_available


# ---- live mode is untouched ----

def test_live_mode_does_not_narrow_the_candidate_pool():
    """`_servable` is a replay-only concession. A live run has no corpus to be
    limited by and must see every candidate the search returned."""
    class LiveReddit:
        served_from_corpus = False

        def has_recorded_thread(self, thread_id):  # pragma: no cover - must not run
            raise AssertionError("live mode must not consult the fixture corpus")

    agent = ResearchAgent(reddit=LiveReddit(), llm=object())
    candidates = [thread(f"t{i}") for i in range(20)]
    assert agent._servable(candidates) == candidates


def test_a_reddit_double_without_the_flag_is_treated_as_live():
    """Test doubles for RedditService need not carry `served_from_corpus`.
    Absent it, behaviour must be exactly what it was before this change."""
    agent = ResearchAgent(reddit=object(), llm=object())
    candidates = [thread(f"t{i}") for i in range(5)]
    assert agent._replaying() is False
    assert agent._servable(candidates) == candidates


def test_narrowing_keeps_everything_when_nothing_is_recorded(replay):
    """Narrowing to an empty pool would trade an explainable "these could not
    be read" for a misleading "no threads found". Keep the list."""
    agent = ResearchAgent(reddit=replay, llm=object())
    candidates = [thread("neverrecorded1"), thread("neverrecorded2")]
    assert agent._servable(candidates) == candidates


# ---- the fixture-layer helper agrees with the real fetch ----

@pytest.mark.parametrize("query,_floor", RECORDED)
def test_has_recorded_thread_agrees_with_what_actually_fetches(query, _floor, replay):
    """`has_recorded_thread` must ask about the SAME request the fetch makes.
    If the two ever drift, selection would trust a thread that then fails."""
    candidates, _ = collect(replay, query)
    for t in candidates[:25]:
        claimed = replay.has_recorded_thread(t.id)
        try:
            replay.get_thread_with_comments(t.id)
            actual = True
        except Exception:  # noqa: BLE001
            actual = False
        assert claimed == actual, f"{t.id}: claimed={claimed} actual={actual}"


def test_has_reports_false_for_an_uncaptured_request():
    assert fx.has("/comments/definitely-not-captured/", {"limit": "100"}) is False


# ---- the failure message names the stage that actually failed ----

def test_a_fetch_failure_is_not_reported_as_a_filter_rejection():
    """`apply_signal_filters` cannot empty a non-empty list — its relaxed path
    ends in `or unique` — so blaming it for zero threads pointed debugging at
    the wrong stage for the entire investigation."""
    replay_note = _unreadable_note(8, replaying=True)
    live_note = _unreadable_note(8, replaying=False)

    for note in (replay_note, live_note):
        assert "signal filter" not in note.lower()
        assert "8" in note
    assert "recorded corpus" in replay_note
    assert "not a problem with your question" in replay_note
    assert "Reddit" in live_note


def test_signal_filters_can_never_empty_a_non_empty_list():
    """The property the message fix relies on, pinned so it stays true."""
    for threads in (
        [thread("a", score=0, comments=0)],
        [thread("a", score=-50), thread("b", score=0)],
        [thread("a", score=1, comments=1), thread("b", score=2, comments=1)],
    ):
        assert RedditService.apply_signal_filters(None, threads)


# ---- the verifier judges evidence, not the absence of exceptions ----

def test_verify_floors_reject_a_zero_thread_replay():
    """A brief with no threads is a broken corpus, and every floor must refuse
    it. This is what the old `--verify` counted as a pass."""
    from scripts.capture_corpus import DEMO_QUERIES, _MIN_THREADS

    assert set(_MIN_THREADS) == set(DEMO_QUERIES), (
        "every demo query needs an evidence floor, or it is unchecked"
    )
    for query, floor in _MIN_THREADS.items():
        assert floor >= 1, f"{query!r} would accept a zero-thread brief"


def test_verify_runs_with_the_cache_disabled():
    """A cached brief would let verification pass without touching the corpus,
    which is the opposite of what it exists to prove."""
    import inspect

    from scripts import capture_corpus

    source = inspect.getsource(capture_corpus.verify)
    assert "use_cache=False" in source
    assert "thread_count" in source and "sources" in source
