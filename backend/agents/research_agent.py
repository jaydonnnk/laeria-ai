"""Research agent — orchestrates Reddit retrieval + LLM synthesis.

Handles both Mode 2 (decision synthesis) and Mode 1 (retrospective mining).
The two share retrieval infrastructure but differ in search strategy and
synthesis prompt.

Mode 2 pipeline (Phase 1):
    identify subreddits (LLM) -> search each (HTML) -> pick best candidates
    -> fetch full threads + comments -> signal filter -> synthesise (LLM JSON)
"""

from __future__ import annotations

from core.logging import get_logger
from core.models import (
    ConfidenceLevel,
    OutcomeSummary,
    RedditThread,
    ResearchBrief,
    SignalQuality,
)
from services.llm import LLMService
from services.reddit import RedditService

logger = get_logger(__name__)

# How many search hits per subreddit to consider, and how many full threads
# (with comments) to actually read across all subreddits. Full fetches are the
# expensive part — one HTTP request each, politely paced.
_SEARCH_LIMIT_PER_SUB = 10
_MAX_SUBREDDITS = 4

_IDENTIFY_SYSTEM = """You identify which subreddits hold the best first-hand \
human discussion for a research query. Respond with JSON only:
{"subreddits": ["name1", "name2", ...], "search_query": "..."}

Rules:
- 2 to 4 subreddits, real and active, no /r/ prefix.
- Prefer niche communities with deep expertise over huge generic ones
  (r/BuyItForLife over r/AskReddit; r/SuggestALaptop over r/technology).
- "search_query": the 2-5 word phrase most likely used in relevant thread
  titles. Not the user's full sentence — the community's phrasing."""

_SYNTHESIS_SYSTEM = """You synthesise Reddit discussions into an honest \
research brief for a purchase/decision. You are working from real thread \
excerpts provided by the user. Respond with JSON only, exactly this schema:

{
  "consensus_pick": "the single option the community most consistently recommends, with one sentence of why. Empty string if no clear consensus.",
  "failure_modes": ["known problems/ways this goes wrong, per the threads"],
  "what_reviewers_miss": ["things these real users mention that review sites don't"],
  "alternatives": ["other options repeatedly mentioned, each with a phrase of context"],
  "red_flags": ["warning signs: astroturfing suspicion, vendor problems, degrading quality"],
  "confidence": "high" | "moderate" | "low",
  "bias_notes": "one or two sentences on sample bias, shilling suspicion, or thin coverage"
}

Rules — these are integrity constraints, not suggestions:
- Only claim what the provided threads actually support. Never invent products,
  prices, or opinions not present in the excerpts.
- Weight by upvotes: a [200 pts] comment outweighs five [2 pts] comments.
- Be suspicious of coordinated praise: same product named with unusual polish
  across unrelated threads, or praised only in low-score comments -> mention in
  red_flags and lower confidence.
- Sarcasm is common. "Oh yeah X is great, if you enjoy wasting money" is
  negative signal.
- confidence: "high" needs multiple independent threads agreeing with strong
  upvotes; "low" whenever threads are few, old, or contradictory. When in
  doubt, choose lower.
- If the threads simply don't answer the query, say so plainly in
  consensus_pick ("The threads found don't clearly answer this") and set
  confidence "low"."""


_CLASSIFY_SYSTEM = """You classify Reddit post titles. The user is researching \
a decision and needs posts that are RETROSPECTIVE OUTCOME REPORTS — someone \
who already made this (or a closely similar) decision reporting how it went.

Respond with JSON only: {"retrospective_ids": ["id1", "id2", ...]}

Include a post id ONLY when the title indicates a first-person report AFTER
the fact: updates ("[UPDATE]", "6 months later", "one year on"), verdicts
("...and I regret it", "best decision I made", "my experience after..."),
or clear outcome reflections.

EXCLUDE:
- Prospective questions ("Is X worth it?", "Should I do X?", "Thinking about X")
- General advice/discussion threads not tied to the author's own outcome
- Posts about a different decision than the one being researched
- News, reviews by outlets, memes"""

_OUTCOMES_SYSTEM = """You synthesise retrospective Reddit posts — real people \
reporting how a decision actually turned out for them — into an honest \
outcomes summary. Respond with JSON only, exactly this schema:

{
  "outcome_split": {"positive": 0.0, "negative": 0.0, "mixed": 0.0},
  "common_positives": ["outcomes people report being glad about"],
  "common_regrets": ["specific regrets people report"],
  "surprising_findings": ["things that contradict conventional wisdom about this decision"],
  "confidence": "high" | "moderate" | "low",
  "sample_bias": "one or two sentences on who self-selects into posting updates and how that skews the picture"
}

Rules — integrity constraints:
- outcome_split fractions must sum to ~1.0 and reflect ONLY the threads
  provided. Count each thread author's own verdict; ignore commenters' votes.
- Only report what post authors actually said. Never invent outcomes.
- surprising_findings must be genuinely present in the threads, not
  general knowledge.
- Update posts skew dramatic (people post extremes, not mediocre outcomes) —
  reflect that in sample_bias.
- confidence: "high" needs many consistent reports; "low" when few threads,
  contradictory outcomes, or the threads only loosely match the decision."""


class ResearchAgent:
    def __init__(
        self, reddit: RedditService | None = None, llm: LLMService | None = None
    ) -> None:
        self._reddit = reddit or RedditService()
        self._llm = llm or LLMService()

    # ---- Mode 2 (Phase 1) ----

    def synthesise_decision(self, query: str, thread_budget: int = 8) -> ResearchBrief:
        """Deep multi-subreddit research → structured consensus brief.

        thread_budget = full thread+comment fetches (the slow, paced part).
        8 threads ≈ 8 requests ≈ 15-20s of Reddit time plus 2 LLM calls.
        """
        # 1. Identify where to look (LLM).
        plan = self._identify_subreddits(query)
        subreddits = plan["subreddits"][:_MAX_SUBREDDITS]
        search_query = plan["search_query"]
        logger.info("research plan: subs=%s query=%r", subreddits, search_query)

        # 2. Search each subreddit (cheap, 1 request each).
        candidates: list[RedditThread] = []
        for sub in subreddits:
            candidates.extend(
                self._reddit.search_subreddit(
                    sub, search_query, time_filter="year", limit=_SEARCH_LIMIT_PER_SUB
                )
            )
        if not candidates:
            # Retry once with the user's own words across the same subs.
            logger.info("no hits for %r; retrying with raw query", search_query)
            for sub in subreddits:
                candidates.extend(
                    self._reddit.search_subreddit(
                        sub, query, time_filter="all", limit=_SEARCH_LIMIT_PER_SUB
                    )
                )
        if not candidates:
            return _empty_brief(subreddits, "No Reddit threads found for this query.")

        # 3. Choose which threads to actually read: engagement-ranked,
        #    spread across subreddits so one sub doesn't dominate.
        chosen = _spread_pick(candidates, thread_budget)

        # 4. Fetch full threads (expensive part), then signal-filter.
        full_threads: list[RedditThread] = []
        for t in chosen:
            try:
                full_threads.append(self._reddit.get_thread_with_comments(t.id))
            except Exception as exc:  # noqa: BLE001
                logger.warning("thread %s fetch failed: %s", t.id, exc)
        filtered = self._reddit.apply_signal_filters(full_threads)
        if not filtered:
            return _empty_brief(subreddits, "Threads found but none passed signal filters.")

        # 5. Synthesise (LLM).
        return self._synthesise(query, filtered, subreddits)

    # ---- internals ----

    def _identify_subreddits(self, query: str) -> dict:
        raw = self._llm.complete_json(
            _IDENTIFY_SYSTEM, f"Research query: {query}", max_tokens=300
        )
        subs = [s.strip().removeprefix("r/") for s in raw.get("subreddits", []) if s.strip()]
        if not subs:
            raise ValueError(f"LLM returned no subreddits for query: {query!r}")
        return {"subreddits": subs, "search_query": raw.get("search_query") or query}

    def _synthesise(
        self, query: str, threads: list[RedditThread], subreddits: list[str]
    ) -> ResearchBrief:
        corpus = _build_corpus(threads)
        raw = self._llm.complete_json(
            _SYNTHESIS_SYSTEM,
            f"Research query: {query}\n\nThread excerpts:\n\n{corpus}",
            max_tokens=2000,
        )
        dates = [t.created_utc for t in threads if t.created_utc]
        date_range = ""
        if dates:
            from datetime import datetime, timezone

            def fmt(ts: float) -> str:
                return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%b %Y")

            date_range = f"{fmt(min(dates))} – {fmt(max(dates))}"

        return ResearchBrief(
            consensus_pick=raw.get("consensus_pick", ""),
            failure_modes=raw.get("failure_modes", []),
            what_reviewers_miss=raw.get("what_reviewers_miss", []),
            alternatives=raw.get("alternatives", []),
            red_flags=raw.get("red_flags", []),
            confidence=_safe_confidence(raw.get("confidence")),
            signal_quality=SignalQuality(
                subreddits_checked=subreddits,
                thread_count=len(threads),
                date_range=date_range,
                bias_notes=raw.get("bias_notes", ""),
            ),
        )

    # ---- Mode 1 (Phase 2) ----

    THIN_COVERAGE_THRESHOLD = 5

    def mine_retrospectives(
        self, decision: str, context: str = "", thread_budget: int = 8
    ) -> OutcomeSummary:
        """Find outcome/update posts from people who made this decision.

        Pipeline: identify subs -> retrospective-template search -> batch
        LLM title classification (keep only actual outcome reports) -> fetch
        full threads -> outcomes synthesis.

        Falls back to a low-confidence result (thin_coverage=True) when fewer
        than THIN_COVERAGE_THRESHOLD retrospective posts are found. Never
        fabricates confidence.
        """
        query = f"{decision} ({context})" if context else decision

        # 1. Where to look + the community's phrasing of the topic.
        plan = self._identify_subreddits(query)
        subreddits = plan["subreddits"][:_MAX_SUBREDDITS]
        topic = plan["search_query"]
        logger.info("retrospective plan: subs=%s topic=%r", subreddits, topic)

        # 2. Template search for outcome-shaped posts.
        candidates = self._reddit.search_retrospective(topic, subreddits)
        if not candidates:
            return _empty_outcomes("No candidate posts found for this decision.")

        # 3. Classify titles in one batched LLM call; keep retrospectives.
        retro_ids = self._classify_retrospectives(decision, candidates)
        retro_candidates = [t for t in candidates if t.id in retro_ids]
        logger.info(
            "classifier kept %d/%d as retrospective", len(retro_candidates), len(candidates)
        )

        thin = len(retro_candidates) < self.THIN_COVERAGE_THRESHOLD
        if not retro_candidates:
            return _empty_outcomes(
                "Candidates found, but none were actual outcome reports "
                "(all prospective questions or irrelevant)."
            )

        # 4. Read the strongest retrospectives in full.
        chosen = sorted(
            retro_candidates, key=lambda t: (t.score, t.num_comments), reverse=True
        )[:thread_budget]
        full_threads: list[RedditThread] = []
        for t in chosen:
            try:
                full_threads.append(self._reddit.get_thread_with_comments(t.id))
            except Exception as exc:  # noqa: BLE001
                logger.warning("thread %s fetch failed: %s", t.id, exc)
        if not full_threads:
            return _empty_outcomes("Retrospective posts found but none could be fetched.")

        # 5. Synthesise outcomes.
        summary = self._synthesise_outcomes(decision, full_threads, len(retro_candidates))
        summary.thin_coverage = thin
        if thin:
            summary.confidence = ConfidenceLevel.LOW
        return summary

    def _classify_retrospectives(
        self, decision: str, candidates: list[RedditThread]
    ) -> set[str]:
        """One batched LLM call: which candidate titles are genuine
        retrospective outcome reports (not prospective questions)?"""
        lines = "\n".join(
            f"{t.id} | r/{t.subreddit} | {t.title[:120]}" for t in candidates[:60]
        )
        raw = self._llm.complete_json(
            _CLASSIFY_SYSTEM,
            f"Decision being researched: {decision}\n\nCandidate posts:\n{lines}",
            max_tokens=1500,
        )
        ids = raw.get("retrospective_ids", [])
        return {str(i) for i in ids if isinstance(i, (str, int))}

    def _synthesise_outcomes(
        self, decision: str, threads: list[RedditThread], retro_count: int
    ) -> OutcomeSummary:
        corpus = _build_corpus(threads)
        raw = self._llm.complete_json(
            _OUTCOMES_SYSTEM,
            f"Decision: {decision}\n\nRetrospective threads (from people who "
            f"made this decision; {retro_count} identified in total, "
            f"{len(threads)} read in full):\n\n{corpus}",
            max_tokens=2500,
        )
        pct = raw.get("outcome_split", {})
        return OutcomeSummary(
            retrospective_count=retro_count,
            pct_positive=float(pct.get("positive", 0.0)),
            pct_negative=float(pct.get("negative", 0.0)),
            pct_mixed=float(pct.get("mixed", 0.0)),
            common_positives=raw.get("common_positives", []),
            common_regrets=raw.get("common_regrets", []),
            surprising_findings=raw.get("surprising_findings", []),
            sample_bias=raw.get("sample_bias", ""),
            confidence=_safe_confidence(raw.get("confidence")),
        )


# ---- module helpers ----

def _spread_pick(candidates: list[RedditThread], budget: int) -> list[RedditThread]:
    """Pick up to `budget` threads, round-robin across subreddits in
    engagement order, so one big subreddit doesn't crowd out the others."""
    by_sub: dict[str, list[RedditThread]] = {}
    for t in sorted(candidates, key=lambda t: (t.num_comments, t.score), reverse=True):
        by_sub.setdefault(t.subreddit, []).append(t)

    picked: list[RedditThread] = []
    seen_ids: set[str] = set()
    while len(picked) < budget and any(by_sub.values()):
        for sub in list(by_sub):
            if by_sub[sub] and len(picked) < budget:
                t = by_sub[sub].pop(0)
                if t.id not in seen_ids:
                    seen_ids.add(t.id)
                    picked.append(t)
            if not by_sub[sub]:
                del by_sub[sub]
    return picked


def _build_corpus(threads: list[RedditThread]) -> str:
    """Rendered thread excerpts for the synthesis prompt. Body and comments
    are truncated to keep total context bounded."""
    from datetime import datetime, timezone

    parts: list[str] = []
    for i, t in enumerate(threads, 1):
        when = (
            datetime.fromtimestamp(t.created_utc, tz=timezone.utc).strftime("%Y-%m")
            if t.created_utc
            else "?"
        )
        lines = [
            f"--- Thread {i} | r/{t.subreddit} | {t.score} pts | "
            f"{t.num_comments} comments | {when} ---",
            f"TITLE: {t.title}",
        ]
        if t.body:
            lines.append(f"POST: {t.body[:1200]}")
        for c in t.top_comments[:10]:
            lines.append(f"COMMENT: {c[:600]}")
        parts.append("\n".join(lines))
    return "\n\n".join(parts)


def _safe_confidence(value: object) -> ConfidenceLevel:
    try:
        return ConfidenceLevel(str(value).lower())
    except ValueError:
        return ConfidenceLevel.LOW


def _empty_brief(subreddits: list[str], note: str) -> ResearchBrief:
    return ResearchBrief(
        consensus_pick="",
        confidence=ConfidenceLevel.LOW,
        signal_quality=SignalQuality(
            subreddits_checked=subreddits, thread_count=0, bias_notes=note
        ),
    )


def _empty_outcomes(note: str) -> OutcomeSummary:
    return OutcomeSummary(
        retrospective_count=0,
        confidence=ConfidenceLevel.LOW,
        thin_coverage=True,
        sample_bias=note,
    )
