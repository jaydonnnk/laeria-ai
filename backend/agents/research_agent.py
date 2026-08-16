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

from agents import relevance
from core.logging import get_logger
from core.models import (
    ConfidenceLevel,
    OutcomeSummary,
    RedditThread,
    ResearchBrief,
    SignalQuality,
    SourceThread,
)
from services.llm import LLMService
from services.reddit import RedditService

logger = get_logger(__name__)

# How many search hits per subreddit to consider, and how many full threads
# (with comments) to actually read across all subreddits. Full fetches are the
# expensive part — one HTTP request each, politely paced.
_SEARCH_LIMIT_PER_SUB = 10
_MAX_SUBREDDITS = 4

# Model-authored FREE TEXT that must clear the output guardrail before it can
# be displayed. Structured values are absent on purpose: `confidence` is one of
# three fixed words and `outcome_split` is coerced to floats, and constraining
# a value to a fixed set is a stronger check than a content filter.
_MODEL_AUTHORED_FIELDS = (
    "consensus_pick",
    "strengths",
    "failure_modes",
    "what_reviewers_miss",
    "alternatives",
    "red_flags",
    "bias_notes",
)
_OUTCOME_AUTHORED_FIELDS = (
    "common_positives",
    "common_regrets",
    "surprising_findings",
    "sample_bias",
)
# The same free text, in a STORED brief. Derived from the list above rather
# than retyped, so the two cannot drift: the only difference is that
# `bias_notes` sits under `signal_quality` once a brief is serialised, and is
# screened separately in `_guard_cached_brief`.
_CACHED_BRIEF_FIELDS = tuple(f for f in _MODEL_AUTHORED_FIELDS if f != "bias_notes")
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
        self,
        reddit: RedditService | None = None,
        llm: LLMService | None = None,
        guardrails=None,  # noqa: ANN001
    ) -> None:
        from services.bedrock_guardrails import get_guardrails

        self._reddit = reddit or RedditService()
        self._llm = llm or LLMService()
        # Injectable so no test needs AWS. Defaults to the shared instance,
        # which is a clean no-op unless BEDROCK_GUARDRAILS_ENABLED is set.
        self._guard = guardrails if guardrails is not None else get_guardrails()

    # ---- Bedrock trust boundaries ----
    #
    # Three small helpers, kept together so the whole safety surface of this
    # agent is readable in one place. See services/bedrock_guardrails.py.

    def _guard_threads(
        self, threads: list[RedditThread]
    ) -> tuple[list[RedditThread], dict[str, str]]:
        """Screen threads. Returns (survivors, sanitized text by thread id).

        Each thread is checked as exactly the text `_build_corpus` would render
        for it, so what was inspected is what would have been sent. source is
        INPUT, not OUTPUT: Bedrock's prompt-attack filter only runs on the
        input side, and injection hidden in someone else's comment is the whole
        reason this call exists.

        THE SANITIZED TEXT IS KEPT, not discarded. Embeddings go out to
        OpenRouter BEFORE the synthesis prompt is assembled and masked, so a
        consumer that re-derived its own text from the raw thread would send
        unmasked personal data through that call — a side door around the
        masking that had already happened. `_safe_views` turns this dict into
        the per-thread view `analyse_threads` embeds.

        The threads themselves are NEVER modified. Sanitized text lives in a
        dict keyed by id, so ids, ordering and the displayed source list stay
        exactly what they were.

        One poisoned thread costs that thread, not the run. An outage raises,
        because synthesising from text nobody checked is the thing this is
        here to prevent.
        """
        from services.bedrock_guardrails import INPUT, GuardrailUnavailable

        if not self._guard.enabled or not threads:
            return list(threads), {}

        verdicts = self._guard.check_many([_build_corpus([t]) for t in threads], INPUT)
        if any(v.unavailable for v in verdicts):
            raise GuardrailUnavailable()

        safe: list[RedditThread] = []
        sanitized: dict[str, str] = {}
        for thread, verdict in zip(threads, verdicts):
            if verdict.blocked:
                # Reddit's own thread id only — never the text that tripped it.
                logger.warning(
                    "guardrail rejected thread %s: %s", thread.id, verdict.reason
                )
                continue
            safe.append(thread)
            sanitized[thread.id] = verdict.text
            if verdict.masked:
                logger.warning(
                    "guardrail masked thread %s: %s", thread.id, verdict.reason
                )
        if len(safe) != len(threads):
            logger.warning(
                "guardrail excluded %d of %d threads", len(threads) - len(safe),
                len(threads),
            )
        return safe, sanitized

    def _guard_cached_brief(self, cached: dict) -> ResearchBrief:
        """Screen a brief coming back off disk before it leaves the agent.

        A cache hit skips synthesis entirely, so it skips the output guardrail
        with it. The cache is disk-backed and survives restarts on purpose,
        which means a brief written before this boundary existed — or while it
        was switched off — can be served long afterwards with its model-authored
        text never having been checked at all.

        Only the model's FREE TEXT is screened. Everything else in a brief is a
        count, a date range, a fixed word, or a Reddit-supplied identifier, and
        running a content filter over those would be theatre. Displayed source
        titles stay as they are, exactly as on the live path — they link to the
        real thread.

        An outage raises, so an unverifiable cached brief is not served.
        """
        if not self._guard.enabled:
            return ResearchBrief.model_validate(cached)

        clean, _ = self._guard.sanitize_model_output(
            cached, _CACHED_BRIEF_FIELDS, "cached research brief"
        )
        # `bias_notes` is nested under signal_quality in a STORED brief, while
        # it is top-level in the raw model response the live path screens. Same
        # text, two shapes, so it needs its own pass rather than being missed.
        signal_quality = clean.get("signal_quality")
        if isinstance(signal_quality, dict):
            clean["signal_quality"], _ = self._guard.sanitize_model_output(
                signal_quality, ("bias_notes",), "cached research brief"
            )
        return ResearchBrief.model_validate(clean)

    def _guard_titles(self, batch: list[RedditThread]) -> tuple[list[RedditThread], str]:
        """Guard search-result titles before a classifying model reads them.

        Returns the surviving threads and the prompt block to send — built
        here and returned, so the checked text is the sent text.
        """
        from services.bedrock_guardrails import INPUT

        lines = [f"{t.id} | r/{t.subreddit} | {t.title[:120]}" for t in batch]
        screened = self._guard.screen_batch(lines, INPUT)
        safe = [batch[i] for i in screened.kept]
        if screened.dropped:
            logger.warning(
                "guardrail excluded %d of %d candidate titles",
                screened.dropped, len(batch),
            )
        return safe, screened.text

    # ---- Mode 2b: research over discussions the USER chose ----

    def synthesise_selected(
        self,
        query: str,
        candidates: list[dict],
        user_id: str = "",
    ) -> ResearchBrief:
        """Research a set of discussions the user picked from live discovery.

        `candidates` are the rows `/research/discover` returned and the user
        ticked: each carries a thread id, a title and the search snippet. What
        Laeria actually holds for each one is decided here, best first:

          1. the full page, if the user sent it from their browser
          2. the full thread, if this exact discussion is in the recorded corpus
          3. the search preview — title and snippet, and nothing pretended

        A preview is thin evidence and is meant to read that way. It is NOT
        given a different confidence rule, a handicap, or a bonus: it goes into
        the same corpus, through the same guardrail, to the same prompt, and
        the synthesis reaches whatever conclusion that much text supports. One
        confidence system, told honestly what it is looking at.
        """
        from services.bedrock_guardrails import INPUT

        query = self._guard.ensure_allowed(query, INPUT, "research query")
        threads, provenance = self._acquire(candidates, user_id)
        if not threads:
            return _empty_brief(
                [], "None of the selected discussions could be read."
            )

        subreddits = sorted({t.subreddit for t in threads if t.subreddit})
        # NO engagement filtering here, deliberately.
        #
        # `apply_signal_filters` exists to prune a pool the MACHINE assembled
        # from a search: with dozens of candidates and no human judgement, it
        # is right to drop the ones nobody upvoted. This path is the opposite
        # situation — a person read these titles and ticked these boxes, so
        # discarding their choice because a thread has few upvotes overrules
        # the user with a heuristic built for a different problem.
        #
        # It also silently deleted the preview tier. A search preview carries
        # no score and no comment count, so the filter's relaxed branch
        # ("keep anything with real engagement") dropped every preview the
        # moment one browser-supplied thread was present — measured: three
        # acquired discussions reached synthesis as one. Dedupe already
        # happened in `_acquire`, which is the part that was actually needed.
        filtered = threads

        # Boundary 2, unchanged. User-supplied transport does not make a
        # stranger's comment trustworthy.
        filtered, safe_text = self._guard_threads(filtered)
        if not filtered:
            return _empty_brief(
                subreddits,
                "The discussions were read, but the safety layer rejected all "
                "of them, so none could be used.",
            )

        brief = self._synthesise(
            query, filtered, subreddits, safe_text,
            provenance={t.id: provenance[t.id] for t in filtered if t.id in provenance},
        )
        return brief

    def _acquire(
        self, candidates: list[dict], user_id: str
    ) -> tuple[list[RedditThread], dict[str, object]]:
        """Best available content for each selected discussion, with how we got
        it. Order is deliberate: browser, then corpus, then preview."""
        from services import ingest
        from services.discovery import Acquisition, Candidate, to_thread

        threads: list[RedditThread] = []
        provenance: dict[str, object] = {}
        seen: set[str] = set()

        for row in candidates:
            tid = str(row.get("thread_id") or "").strip()
            if not tid or tid in seen:
                continue
            seen.add(tid)

            supplied = ingest.get(user_id, tid) if user_id else None
            if supplied is not None:
                threads.append(supplied)
                provenance[tid] = Acquisition.FULL_BROWSER
                continue

            if getattr(self._reddit, "has_recorded_thread", None) and \
                    self._reddit.has_recorded_thread(tid):
                try:
                    threads.append(self._reddit.get_thread_with_comments(tid))
                    provenance[tid] = Acquisition.RECORDED_FULL
                    continue
                except Exception as exc:  # noqa: BLE001
                    logger.warning("recorded thread %s unreadable: %s", tid, exc)

            preview = to_thread(
                Candidate(
                    thread_id=tid,
                    url=str(row.get("url") or ""),
                    title=str(row.get("title") or ""),
                    snippet=str(row.get("snippet") or ""),
                    subreddit=str(row.get("subreddit") or ""),
                )
            )
            if not (preview.title or preview.body):
                continue  # nothing to reason over; drop rather than pad
            threads.append(preview)
            provenance[tid] = Acquisition.SEARCH_PREVIEW

        logger.info(
            "acquired %d of %d selected discussions (%s)",
            len(threads), len(candidates),
            ", ".join(f"{k}={v}" for k, v in _count_levels(provenance).items()) or "none",
        )
        return threads, provenance

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
        from services.bedrock_guardrails import INPUT

        ttl = get_settings().research_cache_seconds

        # Boundary 1: the user's own words, before anything acts on them.
        #
        # Checked BEFORE the cache read, so a refused question cannot be
        # answered by having been asked once already. `query` is rebound to the
        # guarded text, which means every stage below is safe by default; the
        # cache keeps its own key so a stored brief is still findable by the
        # words the user actually typed.
        cache_key = query
        query = self._guard.ensure_allowed(query, INPUT, "research query")

        if use_cache:
            cached = research_cache.get(cache_key, kind="decision", ttl_seconds=ttl)
            if cached is not None:
                return self._guard_cached_brief(cached)
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
            return _empty_brief([], str(exc))
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
            return _empty_brief(subreddits, note)

        # 3. Choose which threads to actually read: how directly a candidate
        #    answers THIS question first, engagement to break ties, still
        #    spread across subreddits so one sub doesn't dominate.
        #
        #    Weights are derived from the candidates themselves, so they have
        #    to be built here rather than once per process: a term is scored by
        #    how rare it is in THIS pool.
        candidates = self._servable(candidates)
        weights = relevance.build_weights(query, search_queries, candidates)
        chosen = _spread_pick(candidates, thread_budget, weights)
        logger.info(
            "selected %d of %d candidates across %d subreddit(s)",
            len(chosen), len(candidates), len({t.subreddit for t in chosen}),
        )

        # 4. Fetch full threads (expensive part), then signal-filter.
        full_threads = self._fetch_threads(chosen)
        # A fetch failure and a filter rejection are different outcomes and
        # need different words. `apply_signal_filters` CANNOT empty a non-empty
        # list — its relaxed path ends in `or unique` — so reporting "none
        # passed signal filters" here blamed the filters for what was really
        # every fetch failing, and sent debugging to the wrong stage.
        if not full_threads:
            return _empty_brief(subreddits, _unreadable_note(len(chosen), self._replaying()))
        filtered = self._reddit.apply_signal_filters(full_threads)
        if not filtered:
            return _empty_brief(
                subreddits, "Threads were read but none passed the signal filters."
            )
        # `apply_signal_filters` returns its survivors in engagement order,
        # which would undo the ranking above for the one thing corpus order
        # decides: the model reads "Thread 1" with the most attention. The
        # filter's own judgement about WHICH threads survive is untouched —
        # only their order is restored to the one selection chose.
        filtered = _in_chosen_order(filtered, chosen)

        # Boundary 2: other people's text, before it becomes model context.
        # A refused thread is left out and the run continues on the rest.
        filtered, safe_text = self._guard_threads(filtered)
        if not filtered:
            return _empty_brief(
                subreddits,
                "Threads were found and read, but the safety layer rejected all "
                "of them, so none could be used.",
            )

        # 5. Synthesise (LLM).
        brief = self._synthesise(query, filtered, subreddits, safe_text)
        if use_cache:
            research_cache.put(cache_key, brief.model_dump(mode="json"), kind="decision")
        return brief

    # ---- internals ----

    def _replaying(self) -> bool:
        """Is this run being answered by the recorded corpus rather than the
        network? Read defensively: a test double for RedditService need not
        carry the flag, and absent it the answer is "no", which leaves every
        behaviour below exactly as it was."""
        return bool(getattr(self._reddit, "served_from_corpus", False))

    def _servable(self, candidates: list[RedditThread]) -> list[RedditThread]:
        """In PURE REPLAY, keep only candidates the corpus can serve in full.

        Deliberately narrow in scope. A frozen corpus holds the search pages
        plus the full text of whichever threads the selector picked AT CAPTURE
        TIME; change how selection ranks and the new picks are threads whose
        body was never captured, so every fetch fails and the corpus silently
        collapses. Narrowing the pool first prevents that.

        It applies ONLY when replay is the whole story. This is not the
        live-primary architecture and must not become it: on the live path the
        corpus is a per-thread fallback (see `_acquire`), not a filter on what
        the user is allowed to be shown.
        """
        from core.config import get_settings

        if get_settings().reddit_source != "fixture" or not self._replaying():
            return candidates
        available = [
            t for t in candidates
            if getattr(self._reddit, "has_recorded_thread", None)
            and self._reddit.has_recorded_thread(t.id)
        ]
        if not available:
            logger.warning(
                "replaying: none of the %d candidates have a recorded full thread",
                len(candidates),
            )
            return candidates
        if len(available) < len(candidates):
            logger.info(
                "replaying: %d of %d candidates have a recorded full thread",
                len(available), len(candidates),
            )
        return available

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

        # Boundary 3: the exact string the model receives. The query was
        # guarded on its own, but what reaches the model is the query wrapped
        # in a label, and one rule everywhere beats a rule with exceptions.
        from services.bedrock_guardrails import GuardrailBlocked

        user_msg, safe = self._guard.screen_prompt(
            f"Research query: {query}", "subreddit planner"
        )
        if not safe:
            raise GuardrailBlocked()
        raw = self._llm.complete_json(_IDENTIFY_SYSTEM, user_msg, max_tokens=400)
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
        self,
        query: str,
        threads: list[RedditThread],
        subreddits: list[str],
        safe_text: dict[str, str] | None = None,
        provenance: dict[str, object] | None = None,
    ) -> ResearchBrief:
        from agents.signal_analysis import analyse_threads

        # The sanitized copy reaches the duplicate detector too. Embeddings go
        # out BEFORE the prompt is assembled, so passing the already-guarded
        # text here is what stops masked personal data leaving through that
        # call instead.
        threads, machine_warnings = analyse_threads(
            threads, self._llm, _safe_views(safe_text or {})
        )
        # Tell the model what it is actually holding. A search preview is two
        # sentences, not a conversation, and a model shown five previews should
        # know it is reasoning about five previews. This is a statement of
        # fact, not an instruction to lower confidence — the existing rules
        # already say thin coverage means low confidence, and adding a second
        # nudge here would be putting a thumb on the scale.
        provenance_note = _provenance_note(provenance or {})
        if provenance_note:
            machine_warnings = [*machine_warnings, provenance_note]
        corpus = _build_corpus(threads)
        analysis_block = (
            "\n\nAUTOMATED SIGNAL ANALYSIS (machine-generated, factor into "
            "red_flags/confidence):\n- " + "\n- ".join(machine_warnings)
            if machine_warnings
            else ""
        )
        # Boundary 3 for synthesis: the finished prompt, checked as one string.
        #
        # Every piece of it has been screened — the question, each thread — but
        # never together, and meaning can live in the relationship between two
        # innocent fragments. Checked ONCE and reused by both halves, which
        # send the identical user message. A refusal here means no brief: every
        # thread passed on its own, so there is no thread to blame and no
        # smaller corpus known to be safe.
        user_msg, prompt_safe = self._guard.screen_prompt(
            f"Research query: {query}\n\nThread excerpts:\n\n{corpus}{analysis_block}",
            "research synthesis",
        )
        if not prompt_safe:
            return _empty_brief(
                subreddits,
                "The assembled discussion text was rejected by the safety "
                "layer, so it was not sent for synthesis.",
            )

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
            # A DIFFERENT string from the one checked above — no analysis
            # block — so it needs its own verdict rather than inheriting one.
            retry_msg, retry_safe = self._guard.screen_prompt(
                f"Research query: {query}\n\nThread excerpts:\n\n{corpus}",
                "research synthesis retry",
            )
            if retry_safe:
                raw = self._llm.complete_json(
                    _SYNTHESIS_SYSTEM, retry_msg, max_tokens=2000
                )
            else:
                logger.warning("retry prompt refused; leaving the brief empty")

        # Boundary 4: the model's own words, before they reach a screen.
        # Free text only — `confidence` is one of three fixed words and is
        # coerced by `_safe_confidence`, which is a stronger check than a
        # content filter.
        raw, _ = self._guard.sanitize_model_output(
            raw, _MODEL_AUTHORED_FIELDS, "research synthesis"
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
            strengths=raw.get("strengths", []),
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
                acquisition=_count_levels(
                    {t.id: lvl for t, lvl in
                     ((t, (provenance or {}).get(t.id)) for t in threads)
                     if lvl is not None}
                ),
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
        from services.bedrock_guardrails import INPUT

        # Same first boundary as Mode 2: the user's words are checked before
        # any model or search sees them, and the guarded text is what the rest
        # of the run uses.
        query = self._guard.ensure_allowed(
            f"{decision} ({context})" if context else decision,
            INPUT,
            "retrospective query",
        )
        decision = self._guard.ensure_allowed(decision, INPUT, "retrospective decision")

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

        # Same external-content boundary as Mode 2.
        full_threads, safe_text = self._guard_threads(full_threads)
        if not full_threads:
            return _empty_outcomes(
                "Retrospective posts were found and read, but the safety layer "
                "rejected all of them."
            )

        # 5. Synthesise outcomes.
        summary = self._synthesise_outcomes(
            decision, full_threads, len(retro_candidates), safe_text
        )
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
        # Titles are external content, and this is the first place they reach
        # a model. Then the assembled prompt gets its own verdict.
        batch, lines = self._guard_titles(batch)
        if not batch:
            return set()
        user_msg, safe = self._guard.screen_prompt(
            f"Decision being researched: {decision}\n\nCandidate posts:\n{lines}",
            "retrospective classifier",
        )
        if not safe:
            return set()
        raw = self._llm.complete_json(_CLASSIFY_SYSTEM, user_msg, max_tokens=1500)
        ids = raw.get("retrospective_ids", [])
        # Models sometimes echo ids with the t3_ fullname prefix — normalise.
        return {
            str(i).removeprefix("t3_") for i in ids if isinstance(i, (str, int))
        }

    def _synthesise_outcomes(
        self,
        decision: str,
        threads: list[RedditThread],
        retro_count: int,
        safe_text: dict[str, str] | None = None,
    ) -> OutcomeSummary:
        from agents.signal_analysis import analyse_threads

        threads, machine_warnings = analyse_threads(
            threads, self._llm, _safe_views(safe_text or {})
        )
        corpus = _build_corpus(threads)
        analysis_block = (
            "\n\nAUTOMATED SIGNAL ANALYSIS (machine-generated, factor into "
            "sample_bias/confidence):\n- " + "\n- ".join(machine_warnings)
            if machine_warnings
            else ""
        )
        user_msg, safe = self._guard.screen_prompt(
            f"Decision: {decision}\n\nRetrospective threads (from people who "
            f"made this decision; {retro_count} identified in total, "
            f"{len(threads)} read in full):\n\n{corpus}{analysis_block}",
            "retrospective synthesis",
        )
        if not safe:
            return _empty_outcomes(
                "The assembled discussion text was rejected by the safety "
                "layer, so it was not sent for synthesis."
            )
        raw = self._llm.complete_json(_OUTCOMES_SYSTEM, user_msg, max_tokens=2500)
        raw, _ = self._guard.sanitize_model_output(
            raw, _OUTCOME_AUTHORED_FIELDS, "retrospective synthesis"
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


def _spread_pick(
    candidates: list[RedditThread],
    budget: int,
    weights: dict[str, float] | None = None,
) -> list[RedditThread]:
    """Pick up to `budget` threads to read in full.

    Threads that say something about the question come first, round-robin
    across subreddits so one big community cannot crowd out the others;
    engagement breaks ties between equally relevant threads. Threads matching
    nothing in the query are the last resort, in engagement order.

    Relevance has to lead. Ranked on engagement alone this returned eight
    threads for a Koss KSC75 question of which none mentioned the KSC75, while
    seven that did went unread — and the round-robin made it worse rather than
    better, since handing every subreddit a guaranteed slot spends that slot on
    the sub's most POPULAR thread whichever question was asked. That is how a
    marketing case study (22 pts, 0 comments) and "college cups" (1 pt) reached
    a corpus about rechargeable batteries and a water bottle.

    `weights` absent — no query context — scores everything zero, which leaves
    one tier ordered by engagement and makes this function byte-for-byte the
    one it replaced. The change is a refinement, not a replacement.
    """
    scored = [(relevance.score(t, weights or {}), t) for t in candidates]
    picked: list[RedditThread] = []
    seen_ids: set[str] = set()

    # Relevance decides which TIER a candidate is in; the round-robin runs
    # inside a tier. Both tiers get one, so cross-community corroboration
    # survives even when nothing matched the query and the fallback is all
    # there is — that spread is what `_coverage_note` reports on and what the
    # synthesis prompt weighs, so it must not depend on relevance succeeding.
    relevant = sorted(
        (st for st in scored if st[0] > 0),
        key=lambda st: (-st[0], -_engagement_key(st[1])),
    )
    # Loosely-related threads keep a thin corpus rather than none: an honest
    # brief over weak evidence beats an empty one, and saying the coverage is
    # thin is the synthesis prompt's job, not this function's.
    rest = sorted((st for st in scored if st[0] <= 0), key=lambda st: -_engagement_key(st[1]))

    for tier in (relevant, rest):
        by_sub: dict[str, list[RedditThread]] = {}
        for _, t in tier:
            by_sub.setdefault(t.subreddit, []).append(t)
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


def _in_chosen_order(
    threads: list[RedditThread], chosen: list[RedditThread]
) -> list[RedditThread]:
    """`threads` reordered to follow `chosen`, membership unchanged.

    Anything not in `chosen` keeps its place at the end rather than being
    dropped — this restores an order, and must never be able to lose evidence.
    """
    rank = {t.id: i for i, t in enumerate(chosen)}
    return sorted(threads, key=lambda t: rank.get(t.id, len(rank)))


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


def _content_parts(block: str) -> tuple[str, str]:
    """(title, post) read back out of a rendered thread block.

    Reading back a block this module generated is exact rather than
    approximate: the prefixes are ours, and Reddit's own parser collapses
    newlines out of titles, bodies and comments before they ever get here, so
    a value can never span two lines.
    """
    title = ""
    post = ""
    for line in block.split("\n"):
        if not title and line.startswith("TITLE: "):
            title = line[len("TITLE: "):]
        elif not post and line.startswith("POST: "):
            post = line[len("POST: "):]
    return title, post


def _safe_views(sanitized: dict[str, str]) -> dict:
    """Per-thread sanitized strings for `analyse_threads`, keyed by thread id.

    The embedding input keeps its existing meaning — title plus the first 600
    characters of the body — and only its SOURCE changes: the guardrail's
    cleaned copy instead of the raw thread. On unsanitized input the two are
    byte-identical, which `test_the_embedding_formula_is_unchanged` pins.
    """
    from agents.signal_analysis import SafeThreadText

    views = {}
    for tid, block in sanitized.items():
        title, post = _content_parts(block)
        views[tid] = SafeThreadText(title=title, embed=f"{title}\n{post[:600]}")
    return views


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


def _in_chosen_order(
    threads: list[RedditThread], chosen: list[RedditThread]
) -> list[RedditThread]:
    """`threads` reordered to follow `chosen`, membership unchanged.

    Anything not in `chosen` keeps its place at the end rather than being
    dropped — this restores an order, and must never be able to lose evidence.
    """
    rank = {t.id: i for i, t in enumerate(chosen)}
    return sorted(threads, key=lambda t: rank.get(t.id, len(rank)))


def _count_levels(provenance: dict) -> dict[str, int]:
    """Acquisition levels tallied by name, for the brief and the logs."""
    counts: dict[str, int] = {}
    for level in provenance.values():
        key = getattr(level, "value", str(level))
        counts[key] = counts.get(key, 0) + 1
    return counts


def _provenance_note(provenance: dict) -> str:
    """One plain sentence telling the model how complete its evidence is."""
    if not provenance:
        return ""
    counts = _count_levels(provenance)
    phrase = {
        "full_browser": "full discussions supplied by the user's browser",
        "recorded_full": "full discussions from the recorded corpus",
        "search_preview": (
            "SEARCH PREVIEWS ONLY — a title and a short snippet, not the "
            "conversation; no comments were available for these"
        ),
        "reddit_oauth": "full discussions from the Reddit API",
    }
    parts = [f"{n} {phrase.get(k, k)}" for k, n in counts.items()]
    return "Evidence completeness: " + "; ".join(parts) + "."


def _unreadable_note(selected: int, replaying: bool) -> str:
    """Why a run that FOUND discussions still has nothing to synthesise.

    Names the stage that actually failed. The two causes need opposite
    responses — a corpus gap is fixed by recapturing, an upstream refusal is
    fixed by waiting — and one message for both hides which one happened.
    """
    if replaying:
        return (
            f"{selected} discussions were selected, but none could be read from "
            "the recorded corpus — this question's discussions were not captured "
            "in full. This is a gap in the recording, not a problem with your "
            "question."
        )
    return (
        f"{selected} discussions were selected, but none could be fetched, so "
        "there was nothing to read. Reddit restricts automated access from "
        "hosted servers; open a discussion in your browser and send it to "
        "Laeria to analyse it."
    )


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
