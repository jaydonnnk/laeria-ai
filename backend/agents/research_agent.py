"""Research agent — orchestrates Reddit retrieval + LLM synthesis.

Handles both Mode 2 (decision synthesis) and Mode 1 (retrospective mining).
The two share retrieval infrastructure but differ in search strategy and
synthesis prompt.

Mode 2 pipeline (Phase 1):
    identify subreddits (LLM) -> search each (HTML) -> pick best candidates
    -> fetch full threads + comments -> signal filter -> synthesise (LLM JSON)
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from core.logging import get_logger
from core.models import (
    ConfidenceLevel,
    EvidenceState,
    OutcomeSummary,
    RedditThread,
    ResearchBrief,
    SignalQuality,
    SourceThread,
)
from services.llm import LLMService
from services.reddit import RedditService

logger = get_logger(__name__)


def effective_query(query: str, context: str = "") -> str:
    """The exact string a Mode 2 run is keyed on.

    Both the research entry points and `/research/act` must derive this
    identically or the action path looks up a cache entry that does not exist:
    a context-bearing decision is researched as "query (context)" and was
    previously acted on as bare "query", so the authoritative brief could never
    be found. One helper, one definition.
    """
    query = query.strip()
    context = context.strip()
    return f"{query} ({context})" if context else query

# How many search hits per subreddit to consider, and how many full threads
# (with comments) to actually read across all subreddits. Full fetches are the
# expensive part — one HTTP request each, politely paced.
_SEARCH_LIMIT_PER_SUB = 10
_MAX_SUBREDDITS = 4
# How many Reddit requests may be in flight at once. This does NOT raise the
# request rate — RedditService paces process-wide off one cursor — it only
# stops each request's latency being stacked on top of every other request's
# pacing gap. Modest on purpose: beyond the gap there is nothing to win.
_FETCH_CONCURRENCY = 4

_IDENTIFY_SYSTEM = """You identify which subreddits hold the best first-hand \
human discussion for a research query. Respond with JSON only:
{"subreddits": ["name1", "name2", ...], "search_queries": ["...", "...", "..."]}

Rules:
- 2 to 4 subreddits, real and active, no /r/ prefix.
- Prefer niche communities with deep expertise over huge generic ones
  (r/BuyItForLife over r/AskReddit; r/SuggestALaptop over r/technology).
- "search_queries": 2-3 DIFFERENT short phrases (2-5 words each) that relevant
  thread titles would actually contain. Vary the wording — different product
  names/aliases, category terms, and community slang — because Reddit search
  is literal keyword matching and a single phrasing misses threads.
  Example for "is the Steam Deck worth buying": ["steam deck worth it",
  "steam deck review", "handheld gaming pc"].
- If the query names a product/model that does not actually exist (a mistaken
  name), include the closest real names in the variants."""

_SYNTHESIS_SYSTEM = """You synthesise Reddit discussions into an honest \
research brief for a purchase/decision. You are working from real thread \
excerpts provided by the user. Respond with JSON only, exactly this schema:

{
  "consensus_pick": "the single option the community most consistently recommends, with one sentence of why. Empty string if no clear consensus.",
  "strengths": ["what real users consistently praise or are glad about, per the threads"],
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
- Report positive and negative signal at the SAME evidentiary standard. Reddit
  engagement skews toward warnings and drama; satisfied users are quieter but
  present in comments — extract their signal too. Do not manufacture negatives
  to seem rigorous, and do not pad strengths to seem balanced: report what is
  actually there, in proportion.
- Weight by upvotes: a [200 pts] comment outweighs five [2 pts] comments.
  "[score hidden]" means the score isn't public yet (recent comment) — judge
  those by content, don't treat them as zero-vote.
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


# The integrity rules are identical for both halves — the split is purely to
# halve wall time, and must not change what either half is allowed to claim.
_SHARED_RULES = """
Rules — these are integrity constraints, not suggestions:
- Only claim what the provided threads actually support. Never invent products,
  prices, or opinions not present in the excerpts.
- Report positive and negative signal at the SAME evidentiary standard. Reddit
  engagement skews toward warnings and drama; satisfied users are quieter but
  present in comments — extract their signal too. Do not manufacture negatives
  to seem rigorous, and do not pad strengths to seem balanced: report what is
  actually there, in proportion.
- Weight by upvotes: a [200 pts] comment outweighs five [2 pts] comments.
  "[score hidden]" means the score isn't public yet (recent comment) — judge
  those by content, don't treat them as zero-vote.
- Sarcasm is common. "Oh yeah X is great, if you enjoy wasting money" is
  negative signal."""

_VERDICT_SYSTEM = (
    """You synthesise Reddit discussions into an honest research brief for a \
purchase/decision. You are working from real thread excerpts provided by the \
user. Respond with JSON only, exactly this schema:

{
  "consensus_pick": "the single option the community most consistently recommends, with one sentence of why. Empty string if no clear consensus.",
  "strengths": ["what real users consistently praise or are glad about, per the threads"],
  "alternatives": ["other options repeatedly mentioned, each with a phrase of context"]
}
"""
    + _SHARED_RULES
    + """
- If the threads simply don't answer the query, say so plainly in
  consensus_pick ("The threads found don't clearly answer this")."""
)

_SCRUTINY_SYSTEM = (
    """You audit Reddit discussions for what could go wrong with a \
purchase/decision, and judge how trustworthy the signal is. You are working \
from real thread excerpts provided by the user. Respond with JSON only, \
exactly this schema:

{
  "failure_modes": ["known problems/ways this goes wrong, per the threads"],
  "what_reviewers_miss": ["things these real users mention that review sites don't"],
  "red_flags": ["warning signs: astroturfing suspicion, vendor problems, degrading quality"],
  "confidence": "high" | "moderate" | "low",
  "bias_notes": "one or two sentences on sample bias, shilling suspicion, or thin coverage"
}
"""
    + _SHARED_RULES
    + """
- Be suspicious of coordinated praise: same product named with unusual polish
  across unrelated threads, or praised only in low-score comments -> mention in
  red_flags and lower confidence.
- confidence: "high" needs multiple independent threads agreeing with strong
  upvotes; "low" whenever threads are few, old, or contradictory. When in
  doubt, choose lower. If the threads simply don't answer the query, set
  confidence "low"."""
)


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


class NoRecordedPlan(RuntimeError):
    """`fixture` mode was asked a question the corpus has no plan for.

    A distinct type because the caller must treat it as "no data for this
    question" — an ordinary, explainable outcome of running on a frozen
    corpus — rather than as a fault. Raised as a bare RuntimeError it reached
    the API as a 500, which is the wrong thing to show someone who simply
    asked something we did not record.
    """


class ResearchAgent:
    def __init__(
        self, reddit: RedditService | None = None, llm: LLMService | None = None
    ) -> None:
        self._reddit = reddit or RedditService()
        self._llm = llm or LLMService()

    # ---- Mode 2 (Phase 1) ----

    def synthesise_decision(
        self, query: str, thread_budget: int = 8, use_cache: bool = True
    ) -> ResearchBrief:
        """Deep multi-subreddit research → structured consensus brief.

        thread_budget = full thread+comment fetches (the slow, paced part).
        8 threads ≈ 8 requests ≈ 15-20s of Reddit time plus 2 LLM calls.

        Repeat queries are served from disk: the threads behind a settled
        purchase question do not change hour to hour, and re-running costs ~20
        paced Reddit requests plus two LLM calls for a byte-identical answer.
        """
        from core.config import get_settings
        from services import research_cache

        ttl = get_settings().research_cache_seconds
        if use_cache:
            cached = research_cache.get(
                query, kind=research_cache.DECISION_CACHE_KIND, ttl_seconds=ttl
            )
            if cached is not None:
                return ResearchBrief.model_validate(cached)
        # 1. Identify where to look (LLM) — including 2-3 query phrasings,
        #    because Reddit search is literal keyword matching.
        #
        # In `fixture` mode an unrecorded query has no plan, and that used to
        # escape as a 500. A query we cannot answer is a normal outcome of
        # running on a frozen corpus, not a server fault — it degrades to the
        # same honest empty brief as every other "we have no data" path, so an
        # off-script question from the audience gets an explanation instead of
        # an error page.
        try:
            plan = self._identify_subreddits(query)
        except NoRecordedPlan as exc:
            logger.warning("no recorded plan for %r", query)
            return _empty_brief([], str(exc), EvidenceState.NOT_IN_CORPUS)
        subreddits = plan["subreddits"][:_MAX_SUBREDDITS]
        search_queries = plan["search_queries"]
        logger.info("research plan: subs=%s queries=%s", subreddits, search_queries)

        # 2. Search every phrasing in every subreddit (cheap, 1 request each),
        #    dedupe across variants.
        # Concurrent, but NOT faster per Reddit: the pacing cursor in
        # RedditService is process-wide, so these still leave at one request
        # per gap. What overlaps is each request's latency, which was
        # previously stacked on top of every gap — 9 searches cost 9x(gap+RTT)
        # serially and 9 gaps concurrently.
        candidates: list[RedditThread] = []
        with ThreadPoolExecutor(max_workers=_FETCH_CONCURRENCY) as pool:
            futures = [
                pool.submit(
                    self._reddit.search_subreddit,
                    sub, sq, time_filter="year", limit=_SEARCH_LIMIT_PER_SUB,
                )
                for sub in subreddits
                for sq in search_queries
            ]
            for fut in futures:
                try:
                    candidates.extend(fut.result())
                except Exception as exc:  # noqa: BLE001
                    logger.warning("search failed: %s", exc)
        if not candidates:
            # Retry once with the user's own words, all-time, across the subs.
            logger.info("no hits for %s; retrying with raw query", search_queries)
            for sub in subreddits:
                candidates.extend(
                    self._reddit.search_subreddit(
                        sub, query, time_filter="all", limit=_SEARCH_LIMIT_PER_SUB
                    )
                )
        seen_ids: set[str] = set()
        candidates = [
            t for t in candidates if not (t.id in seen_ids or seen_ids.add(t.id))
        ]
        if not candidates:
            # "No threads found" is true but reads as "your question has no
            # answer", which is the wrong conclusion when the real cause is
            # that Reddit refused every request. Name the actual reason:
            # the two cases need opposite responses from the user (rephrase
            # vs. nothing they can do), and conflating them makes a total
            # upstream outage look like a bad query.
            live_ok, detail = self._reddit.probe_live()
            note = (
                "No Reddit threads found for this query."
                if live_ok
                else (
                    f"Reddit is not serving requests right now ({detail}). "
                    "Only previously recorded queries can be answered until "
                    "access is restored — this is not a problem with your "
                    "question."
                )
            )
            # The two cases need opposite responses from the user (rephrase vs.
            # nothing they can do), so they are also two different states.
            return _empty_brief(
                subreddits,
                note,
                EvidenceState.NO_EVIDENCE if live_ok else EvidenceState.SOURCE_UNAVAILABLE,
            )

        # 3. Choose which threads to actually read: engagement-ranked,
        #    spread across subreddits so one sub doesn't dominate.
        chosen = _spread_pick(candidates, thread_budget)

        # 4. Fetch full threads (expensive part), then signal-filter.
        #    Results are collected in `chosen` order rather than completion
        #    order, so the corpus handed to the LLM stays engagement-ranked.
        full_threads = self._fetch_threads(chosen)
        filter_result = self._reddit.apply_signal_filters(full_threads)
        if not filter_result.threads:
            # Reaching here does NOT mean the filters rejected everything.
            # `apply_signal_filters` relaxes until something survives and only
            # returns empty for empty input, so an empty result means the full
            # -thread fetches produced nothing — every one of them failed. The
            # note says what actually happened rather than blaming a stage
            # that never got the chance to reject anything.
            return _empty_brief(
                subreddits,
                "Search results were found, but no full thread could be "
                "retrieved for synthesis.",
                EvidenceState.NO_USABLE_EVIDENCE,
            )

        # 5. Synthesise (LLM), then apply the deterministic confidence ceiling.
        brief = self._synthesise(query, filter_result, subreddits)
        if use_cache:
            research_cache.put(
                query,
                brief.model_dump(mode="json"),
                kind=research_cache.DECISION_CACHE_KIND,
            )
        return brief

    # ---- internals ----

    def _fetch_threads(self, chosen: list[RedditThread]) -> list[RedditThread]:
        """Full thread+comment fetches, overlapped.

        Order follows `chosen`, not completion, because the corpus is handed
        to the LLM in this order and `_spread_pick` already ranked it. A
        failed fetch is dropped with a warning — one dead thread must not cost
        the whole brief.
        """
        results: dict[str, RedditThread] = {}
        with ThreadPoolExecutor(max_workers=_FETCH_CONCURRENCY) as pool:
            futures = {
                pool.submit(self._reddit.get_thread_with_comments, t.id): t
                for t in chosen
            }
            for fut, t in futures.items():
                try:
                    results[t.id] = fut.result()
                except Exception as exc:  # noqa: BLE001
                    logger.warning("thread %s fetch failed: %s", t.id, exc)
        return [results[t.id] for t in chosen if t.id in results]

    def _identify_subreddits(self, query: str) -> dict:
        # The plan decides which URLs get fetched, so it is part of the
        # recorded corpus — replaying HTML against a freshly-generated plan
        # would miss every fixture. See services/reddit_fixtures.
        from core.config import get_settings
        from services import reddit_fixtures as fx

        mode = get_settings().reddit_source
        if mode in ("fixture", "live_then_fixture"):
            cached = fx.load_plan(query)
            if cached is not None:
                return {
                    "subreddits": cached["subreddits"],
                    "search_queries": cached["search_queries"],
                }
            if mode == "fixture":
                raise NoRecordedPlan(
                    "Reddit is not reachable and this exact question is not in "
                    "the recorded corpus, so it cannot be answered right now. "
                    "This is not a problem with your question — logged-out "
                    "Reddit access is blocked upstream."
                )

        raw = self._llm.complete_json(
            _IDENTIFY_SYSTEM, f"Research query: {query}", max_tokens=400
        )
        subs = [s.strip().removeprefix("r/") for s in raw.get("subreddits", []) if s.strip()]
        if not subs:
            raise ValueError(f"LLM returned no subreddits for query: {query!r}")
        queries = [q.strip() for q in raw.get("search_queries", []) if str(q).strip()]
        # Tolerate old-style single search_query responses.
        if not queries and raw.get("search_query"):
            queries = [str(raw["search_query"])]
        plan = {"subreddits": subs, "search_queries": (queries or [query])[:3]}
        if mode == "record":
            fx.save_plan(query, plan)
        return plan

    def _synthesise(
        self, query: str, filter_result, subreddits: list[str]
    ) -> ResearchBrief:
        from agents.confidence import EvidenceStats, assess
        from agents.signal_analysis import analyse_threads

        analysis = analyse_threads(filter_result.threads, self._llm)
        threads = analysis.threads
        machine_warnings = analysis.warnings
        corpus = _build_corpus(threads)
        analysis_block = (
            "\n\nAUTOMATED SIGNAL ANALYSIS (machine-generated, factor into "
            "red_flags/confidence):\n- " + "\n- ".join(machine_warnings)
            if machine_warnings
            else ""
        )
        user_msg = f"Research query: {query}\n\nThread excerpts:\n\n{corpus}{analysis_block}"

        # Output tokens dominate this call — measured at ~59s for one ~2000
        # token JSON body, which was the single largest cost in a query. The
        # schema splits cleanly in two halves that do not need each other, so
        # they run concurrently and the wall time is the slower of the two
        # rather than their sum.
        #
        # The split is chosen so `confidence` stays with `red_flags`: the
        # prompt requires suspected astroturfing to LOWER confidence, so those
        # two fields have to be decided by the same call.
        with ThreadPoolExecutor(max_workers=2) as pool:
            verdict_f = pool.submit(
                self._llm.complete_json, _VERDICT_SYSTEM, user_msg, 1200
            )
            scrutiny_f = pool.submit(
                self._llm.complete_json, _SCRUTINY_SYSTEM, user_msg, 1200
            )
            # Second bound, independent of the HTTP timeout: whatever goes
            # wrong inside the client, a half cannot hold the whole brief
            # hostage. Derived from the client's own worst case rather than
            # guessed at — set below timeout x attempts, this would abandon a
            # retry that was about to succeed; set far above it, it stops
            # bounding anything.
            deadline = self._llm.worst_case_seconds + 15
            raw: dict = {}
            for fut, half in ((verdict_f, "verdict"), (scrutiny_f, "scrutiny")):
                try:
                    raw.update(fut.result(timeout=deadline))
                except Exception as exc:  # noqa: BLE001
                    # Half a brief beats no brief: a failed half leaves its
                    # fields empty and the rest of the report still renders.
                    logger.warning("%s half of synthesis failed: %s", half, exc)

        # Guard against a transient model flake: valid JSON with every field
        # blank despite having real threads to work from. One retry, unsplit.
        if not any(
            raw.get(k)
            for k in ("consensus_pick", "strengths", "failure_modes",
                      "alternatives", "red_flags", "bias_notes")
        ):
            logger.warning("synthesis returned all-empty fields; retrying once")
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

        # The model read the threads and judged whether people agree. That is
        # where its authority ends: how much confidence this evidence SHAPE is
        # allowed to earn is decided below, deterministically, and the verdict
        # is the more conservative of the two. Structure can only ever lower.
        consensus_pick = raw.get("consensus_pick", "")
        # Subreddits actually present in the corpus that was synthesised —
        # not the ones the planner hoped to read. A community that was
        # searched and yielded nothing did not contribute evidence, and saying
        # otherwise is contradicted by the source list on the same screen.
        represented = sorted({t.subreddit for t in threads if t.subreddit})
        stats = EvidenceStats(
            usable_thread_count=len(threads),
            strong_thread_count=filter_result.strong_thread_count,
            represented_subreddits=tuple(represented),
            filters_relaxed=filter_result.relaxed,
            cross_author_duplicate_count=analysis.cross_author_duplicate_count,
            similarity_analysis_available=analysis.similarity_analysis_available,
            has_consensus_pick=bool(consensus_pick.strip()),
        )
        outcome = assess(_safe_confidence(raw.get("confidence")), stats)
        logger.info(
            "confidence: semantic=%s ceiling=%s final=%s (%d reasons)",
            outcome.semantic.value, outcome.ceiling.value, outcome.final.value,
            len(outcome.reasons),
        )

        return ResearchBrief(
            consensus_pick=consensus_pick,
            strengths=raw.get("strengths", []),
            failure_modes=raw.get("failure_modes", []),
            what_reviewers_miss=raw.get("what_reviewers_miss", []),
            alternatives=raw.get("alternatives", []),
            red_flags=raw.get("red_flags", []),
            confidence=outcome.final,
            semantic_confidence=outcome.semantic,
            structural_ceiling=outcome.ceiling,
            confidence_reasons=list(outcome.reasons),
            signal_quality=SignalQuality(
                subreddits_checked=subreddits,
                subreddits_represented=represented,
                usable_thread_count=len(threads),
                thread_count=len(threads),
                strong_thread_count=filter_result.strong_thread_count,
                filters_relaxed=filter_result.relaxed,
                coordinated_posting_suspected=analysis.cross_author_duplicate_count > 0,
                duplicate_threads_collapsed=analysis.collapsed_same_author_count,
                similarity_analysis_available=analysis.similarity_analysis_available,
                evidence_state=EvidenceState.OK,
                date_range=date_range,
                bias_notes=raw.get("bias_notes", ""),
            ),
            sources=_to_sources(threads),
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

        # 1. Where to look + the community's phrasings of the topic.
        plan = self._identify_subreddits(query)
        subreddits = plan["subreddits"][:_MAX_SUBREDDITS]
        topics = plan["search_queries"]
        logger.info("retrospective plan: subs=%s topics=%s", subreddits, topics)

        # 2. Template search for outcome-shaped posts (primary phrasing for
        #    the per-sub templates; every phrasing gets a site-wide pass).
        candidates = self._reddit.search_retrospective(
            topics[0], subreddits, extra_topics=topics[1:]
        )
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
        chosen = sorted(retro_candidates, key=_engagement_key, reverse=True)[:thread_budget]
        full_threads = self._fetch_threads(chosen)
        if not full_threads:
            return _empty_outcomes("Retrospective posts found but none could be fetched.")

        # 5. Synthesise outcomes.
        summary = self._synthesise_outcomes(decision, full_threads, len(retro_candidates))
        summary.thin_coverage = thin
        if thin:
            summary.confidence = ConfidenceLevel.LOW
        summary.sources = _to_sources(full_threads)
        return summary

    def _classify_retrospectives(
        self, decision: str, candidates: list[RedditThread]
    ) -> set[str]:
        """One batched LLM call: which candidate titles are genuine
        retrospective outcome reports (not prospective questions)?"""
        # Cap the batch at 60 titles — but rank by engagement first, so the
        # cut drops the weakest candidates instead of whichever subreddit
        # happened to be searched last.
        batch = sorted(candidates, key=_engagement_key, reverse=True)[:60]
        lines = "\n".join(f"{t.id} | r/{t.subreddit} | {t.title[:120]}" for t in batch)
        raw = self._llm.complete_json(
            _CLASSIFY_SYSTEM,
            f"Decision being researched: {decision}\n\nCandidate posts:\n{lines}",
            max_tokens=1500,
        )
        ids = raw.get("retrospective_ids", [])
        # Models sometimes echo ids with the t3_ fullname prefix — normalise.
        return {
            str(i).removeprefix("t3_") for i in ids if isinstance(i, (str, int))
        }

    def _synthesise_outcomes(
        self, decision: str, threads: list[RedditThread], retro_count: int
    ) -> OutcomeSummary:
        from agents.signal_analysis import analyse_threads

        # Mode 1 is unchanged by the Phase A structural work: it reads the
        # prose half of the analysis exactly as before. Its confidence already
        # has a deterministic floor of its own (`thin_coverage` in
        # `mine_retrospectives`), and widening the structural policy to
        # retrospectives is deliberately out of scope here.
        analysis = analyse_threads(threads, self._llm)
        threads = analysis.threads
        machine_warnings = analysis.warnings
        corpus = _build_corpus(threads)
        analysis_block = (
            "\n\nAUTOMATED SIGNAL ANALYSIS (machine-generated, factor into "
            "sample_bias/confidence):\n- " + "\n- ".join(machine_warnings)
            if machine_warnings
            else ""
        )
        raw = self._llm.complete_json(
            _OUTCOMES_SYSTEM,
            f"Decision: {decision}\n\nRetrospective threads (from people who "
            f"made this decision; {retro_count} identified in total, "
            f"{len(threads)} read in full):\n\n{corpus}{analysis_block}",
            max_tokens=2500,
        )
        pct = raw.get("outcome_split", {})
        return OutcomeSummary(
            retrospective_count=retro_count,
            threads_read=len(threads),
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

def _engagement_key(t: RedditThread) -> int:
    """Ranking signal for search-page candidates. Search-page scores are
    unreliable (often 0) and comment counts alone favour controversy — a
    400-comment flame war would outrank a 2000-pt beloved guide. Summing both
    lets a real score dominate when present and comments carry the rest."""
    return t.score + t.num_comments


def _spread_pick(candidates: list[RedditThread], budget: int) -> list[RedditThread]:
    """Pick up to `budget` threads, round-robin across subreddits in
    engagement order, so one big subreddit doesn't crowd out the others."""
    by_sub: dict[str, list[RedditThread]] = {}
    for t in sorted(candidates, key=_engagement_key, reverse=True):
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


def _to_sources(threads: list[RedditThread]) -> list[SourceThread]:
    """Threads the synthesis actually used, as user-facing citations with
    canonical reddit.com links."""
    return [
        SourceThread(
            id=t.id,
            subreddit=t.subreddit,
            title=t.title,
            url=f"https://www.reddit.com/comments/{t.id}/",
            score=t.score,
            num_comments=t.num_comments,
        )
        for t in threads
    ]


def _safe_confidence(value: object) -> ConfidenceLevel:
    try:
        return ConfidenceLevel(str(value).lower())
    except ValueError:
        return ConfidenceLevel.LOW


def _empty_brief(
    subreddits: list[str], note: str, state: EvidenceState
) -> ResearchBrief:
    """A brief with no evidence behind it.

    `state` is required rather than defaulted: every caller knows exactly why
    it has nothing, and that reason is the most useful thing it can pass on. A
    default would let a new no-evidence branch silently inherit someone else's
    explanation, which is the failure this field exists to end.

    The reason is derived FROM that state rather than written once for all of
    them. A single generic sentence read as "retrieval and filtering rejected
    everything", which is untrue of a corpus miss (nothing was retrieved), of
    an outage (nothing was reachable), and of an empty search (nothing was
    found) — three of the four branches that land here.
    """
    from agents.confidence import evidence_state_reason

    return ResearchBrief(
        consensus_pick="",
        confidence=ConfidenceLevel.LOW,
        semantic_confidence=ConfidenceLevel.LOW,
        structural_ceiling=ConfidenceLevel.LOW,
        confidence_reasons=evidence_state_reason(state),
        signal_quality=SignalQuality(
            subreddits_checked=subreddits,
            subreddits_represented=[],
            usable_thread_count=0,
            thread_count=0,
            evidence_state=state,
            bias_notes=note,
        ),
    )


def _empty_outcomes(note: str) -> OutcomeSummary:
    return OutcomeSummary(
        retrospective_count=0,
        confidence=ConfidenceLevel.LOW,
        thin_coverage=True,
        sample_bias=note,
    )
