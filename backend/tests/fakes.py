"""Shared fakes for the Mode 2 research pipeline.

One definition of each seam the agent talks to, so two test files cannot drift
into disagreeing about what the pipeline calls or what a stage returns. When a
new stage is added to the pipeline — as the relevance screen was — it is
modelled here once and every test that drives the pipeline sees it.

Nothing here touches the network, OpenRouter, Reddit, Supabase or the fixture
corpus on disk.
"""

from __future__ import annotations

import re

from core.models import RedditThread


def thread(
    tid: str,
    sub: str,
    score: int = 100,
    comments: int = 40,
    title: str = "",
    author: str = "",
) -> RedditThread:
    """A thread that clears the strong-evidence bar (score >= 10, comments >= 3)."""
    return RedditThread(
        id=tid,
        subreddit=sub,
        title=title or f"thread {tid}",
        body=f"body of {tid}",
        url=f"https://old.reddit.com/comments/{tid}/",
        score=score,
        num_comments=comments,
        created_utc=1_700_000_000.0,
        author=author or f"u_{tid}",
        top_comments=[f"[{score} pts] a comment on {tid}"],
    )


def strong_corpus() -> list[RedditThread]:
    """8 threads across 4 communities — nothing here should cap confidence."""
    subs = ["SteamDeck", "HandheldPC", "patientgamers", "gaming"]
    return [thread(f"t{i}", subs[i % 4]) for i in range(8)]


def candidate_ids(user_msg: str) -> list[str]:
    """The ids the agent listed in a batched classification prompt.

    Read back out of the prompt rather than captured from the fixture, so the
    fake answers about the posts it was actually shown — a screen that replied
    about some other set is a case the real pipeline has to handle, and it
    should only happen when a test asks for it.
    """
    return re.findall(r"^(\S+) \| r/", user_msg, re.M)


class FakeReddit:
    """Stands in for RedditService at the seams the agent actually uses."""

    def __init__(
        self,
        candidates: list[RedditThread] | None = None,
        threads: list[RedditThread] | None = None,
        live_ok: bool = True,
    ):
        self._candidates = candidates if candidates is not None else strong_corpus()
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


class FetchAlwaysFails(FakeReddit):
    """Search returns candidates; every full-thread fetch dies.

    The real production route to an empty synthesis corpus: `_fetch_threads`
    drops failures with a warning, so the corpus arrives empty even though
    search found plenty.
    """

    def get_thread_with_comments(self, thread_id, max_comments=15):
        raise RuntimeError("thread fetch failed")


class FakeLLM:
    """Scripted planner, relevance screen and synthesis.

    Defaults are the benign case: every candidate is relevant, embeddings
    work, both synthesis halves answer. Each argument degrades exactly one
    stage, so a failing test names the stage that broke.
    """

    def __init__(
        self,
        subreddits: list[str] | None = None,
        confidence: str = "high",
        consensus_pick: str = "The Steam Deck OLED, for its screen and battery.",
        embed_fails: bool = False,
        drop_half: str | None = None,
        red_flags: list[str] | None = None,
        bias_notes: str = "enthusiast-skewed sample",
        relevant_ids: list[str] | None = None,
        relevance_reply: dict | None = None,
        relevance_fails: bool = False,
    ):
        self._subreddits = subreddits or ["SteamDeck", "HandheldPC", "patientgamers", "gaming"]
        self._confidence = confidence
        self._pick = consensus_pick
        self._embed_fails = embed_fails
        self._drop_half = drop_half
        self._red_flags = red_flags if red_flags is not None else []
        self._bias_notes = bias_notes
        # None = "everything you showed me is relevant". A list keeps only
        # those ids; `relevance_reply` overrides the whole JSON body, for the
        # malformed-answer cases.
        self._relevant_ids = relevant_ids
        self._relevance_reply = relevance_reply
        self._relevance_fails = relevance_fails
        self.worst_case_seconds = 1

    def complete_json(self, system: str, user: str, max_tokens: int = 0, **kw) -> dict:
        if "identify which subreddits" in system:
            return {"subreddits": self._subreddits, "search_queries": ["q1", "q2"]}
        if "screen Reddit search results for relevance" in system:
            if self._relevance_fails:
                raise RuntimeError("relevance screen unavailable")
            if self._relevance_reply is not None:
                return self._relevance_reply
            ids = (
                candidate_ids(user)
                if self._relevant_ids is None
                else self._relevant_ids
            )
            return {"relevant_ids": ids}
        if "audit Reddit discussions" in system:          # scrutiny half
            if self._drop_half == "scrutiny":
                raise RuntimeError("scrutiny half unavailable")
            return {
                "failure_modes": ["fan noise"],
                "what_reviewers_miss": ["case fit"],
                "red_flags": list(self._red_flags),
                "confidence": self._confidence,
                "bias_notes": self._bias_notes,
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
