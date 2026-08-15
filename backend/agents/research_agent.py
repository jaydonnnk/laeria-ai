"""Research agent — orchestrates Reddit retrieval + LLM synthesis.

Handles both Mode 2 (decision synthesis) and Mode 1 (retrospective mining).
The two share retrieval infrastructure but differ in search strategy and
synthesis prompt.

Mode 2 pipeline (Phase 1):
    guard the query -> identify subreddits (LLM) -> search each (HTML) ->
    screen for relevance (LLM) -> pick best candidates -> fetch full threads +
    comments -> signal filter -> guard the evidence -> synthesise (LLM JSON)
    -> guard the output

Three of those steps are the Bedrock guardrail, and they sit at the three
places text crosses a trust boundary: the user's question going in, Reddit's
text becoming model context, and the model's words coming back out. See
services/bedrock_guardrails.py. With guardrails disabled every one of them is
a no-op and the pipeline is exactly what it was.

The evidence guard keeps the sanitized copy of each thread it clears, and that
copy is what the synthesis prompt, the embedding call and the duplicate
warnings all read. The embedding call goes out BEFORE the prompt is assembled,
so anything that rebuilt its own text from the raw thread at that point would
ship unmasked personal data to a third party the guardrail had just cleaned.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

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
# How many search hits the relevance screen judges in one call. Ranked by
# engagement first, so the cut drops the weakest candidates rather than
# whichever subreddit was searched last — and the thread budget is 8, so the
# tail was never going to be read anyway.
_RELEVANCE_BATCH = 60

# Every field of a synthesis response the MODEL wrote, and therefore everything
# that has to clear the output guardrail before it can be shown or acted on.
# `confidence` is deliberately absent: it is one of three fixed words, and the
# structural policy decides what it is finally allowed to be anyway.
_MODEL_AUTHORED_FIELDS = (
    "consensus_pick",
    "strengths",
    "failure_modes",
    "what_reviewers_miss",
    "alternatives",
    "red_flags",
    "bias_notes",
)

# The same idea for Mode 1: everything in an outcomes summary the model wrote.
_OUTCOME_AUTHORED_FIELDS = (
    "common_positives",
    "common_regrets",
    "surprising_findings",
    "sample_bias",
)

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
  "bias_notes": "one or two sentences on WHO is talking and how that skews the picture — self-selection, enthusiast skew, shilling suspicion"
}

Rules — these are integrity constraints, not suggestions:
- Do NOT make claims about the SHAPE of the evidence: how many threads there
  are, how many communities they span, or whether cross-community
  corroboration exists. Those are counted from the corpus and shown to the
  user separately, so a guess here can only contradict them. Write about what
  people SAID.
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
  "bias_notes": "one or two sentences on WHO is talking and how that skews the picture — self-selection, enthusiast skew, shilling suspicion"
}
"""
    + _SHARED_RULES
    + """
- Do NOT make claims about the SHAPE of the evidence: how many threads there
  are, how many communities they span, or whether cross-community
  corroboration exists. Those are counted from the corpus and shown to the
  user separately, so a guess here can only contradict them — and a claim that
  contradicts the count is removed before anyone sees it. Write about what
  people SAID; the confidence field is where thin coverage belongs.
- Be suspicious of coordinated praise: same product named with unusual polish
  across unrelated threads, or praised only in low-score comments -> mention in
  red_flags and lower confidence.
- confidence: "high" needs multiple independent threads agreeing with strong
  upvotes; "low" whenever threads are few, old, or contradictory. When in
  doubt, choose lower. If the threads simply don't answer the query, set
  confidence "low"."""
)


_RELEVANCE_SYSTEM = """You screen Reddit search results for relevance to a \
research question. Reddit search is literal keyword matching, so the results \
routinely contain threads about a completely different topic that merely share \
a word with the query.

Respond with JSON only: {"relevant_ids": ["id1", "id2", ...]}

Include a post id when the title suggests the thread COULD carry discussion
bearing on the question — the same product, the same category, the same
decision, or the experience of people who made it.

EXCLUDE only posts clearly about something else. For "best mechanical keyboard
under $100 in Singapore", exclude "where to buy laptops in SG" and "best quiet
area in SG for long term stay"; keep "cheap keyboard recommendations" and
"which switches for a budget board".

Be conservative: when a title is vague or ambiguous, INCLUDE it. Dropping a
relevant thread costs more than keeping a doubtful one. Return every id you
keep, even if that is all of them."""


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


@dataclass(frozen=True)
class RelevanceScreen:
    """What the relevance screen decided about one candidate set.

    `screened` is the honest record of whether the check actually ran. False
    means the candidates are unfiltered, NOT that they were all found
    relevant — the confidence policy depends on being able to tell those
    apart, in the same way it already does for similarity analysis.
    """

    threads: list[RedditThread]
    screened: bool = False
    # Dropped as off-topic. Counts ONLY that: a candidate refused by the
    # guardrail was not irrelevant, and folding the two together would report
    # a safety exclusion as a relevance judgement.
    rejected: int = 0
    # Dropped because the safety layer refused the title.
    unsafe: int = 0


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
        # which is a no-op unless BEDROCK_GUARDRAILS_ENABLED is set.
        self._guard = guardrails if guardrails is not None else get_guardrails()

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

        # 0. The user's own words, before anything acts on them.
        #
        # Checked BEFORE the cache read, not after: a refused question must not
        # be answerable by having been asked once already. Raises on a refusal,
        # which the routes turn into a plain-language reply — nothing below
        # this line runs, so no LLM is prompted, no subreddit is searched and
        # no action can be proposed.
        #
        # `query` is REBOUND to the guarded text, so every stage below is safe
        # by default and a future line added here cannot accidentally use the
        # unguarded words. If the guardrail masked personal data out of the
        # question, the masked form is what reaches OpenRouter and Reddit.
        #
        # The cache keeps its own key, the words the user actually typed, so a
        # stored brief is still findable by `/research/act` — which looks it up
        # by exactly those words.
        cache_key = query
        query = self._guard.ensure_allowed(query, INPUT, "research query")

        if use_cache:
            cached = research_cache.get(
                cache_key, kind=research_cache.DECISION_CACHE_KIND, ttl_seconds=ttl
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

        # 3. Screen for relevance BEFORE anything is read.
        #
        # Reddit search is literal keyword matching, so a search for a cheap
        # keyboard in Singapore returns "where to buy laptops in SG" and "best
        # quiet area in SG for long term stay" alongside the real hits. Those
        # threads used to be fetched, filtered on engagement alone (which they
        # pass easily — they are popular threads, just about something else),
        # and then counted as usable evidence: they inflated the thread count,
        # added their subreddits to the represented communities, and so raised
        # the confidence the evidence shape was allowed to earn.
        #
        # Screening here rather than after fetching is deliberate: an off-topic
        # thread that is never read cannot reach any downstream count, and the
        # fetches it would have consumed go to real candidates instead.
        screen = self._screen_relevance(query, candidates)
        if not screen.threads:
            # Nothing survived. WHY it did not survive decides both the state
            # and the sentence: "none of these were about your question" and
            # "the safety layer refused these" are different findings, and
            # telling someone the first when the second happened sends them
            # rephrasing a question that was never the problem.
            if screen.unsafe and not screen.rejected:
                return _empty_brief(
                    subreddits,
                    "Threads were found, but the safety layer rejected all of "
                    "them before any could be read.",
                    EvidenceState.UNSAFE_EVIDENCE,
                )
            note = (
                "Threads were found, but none of them were about this "
                "question, so there was nothing relevant to read."
            )
            if screen.unsafe:
                note += (
                    f" A further {screen.unsafe} were rejected by the safety "
                    "layer."
                )
            return _empty_brief(subreddits, note, EvidenceState.NO_EVIDENCE)

        # 4. Choose which threads to actually read: engagement-ranked,
        #    spread across subreddits so one sub doesn't dominate.
        chosen = _spread_pick(screen.threads, thread_budget)

        # 5. Fetch full threads (expensive part), then signal-filter.
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

        # 6. Guard the external content before it becomes model context.
        #
        # This is the boundary the hackathon security briefing calls out:
        # somebody else's text, retrieved by us, about to be read by our model
        # as if it were evidence. A Reddit comment can carry instructions aimed
        # at the agent rather than opinions aimed at people.
        #
        # Per thread, not per run: one poisoned thread costs that thread. What
        # survives becomes the corpus, and because the authoritative evidence
        # set is built from exactly this list, a rejected thread cannot appear
        # in the counts, the communities, the sources or the confidence.
        safe_threads, unsafe_full, safe_content = self._guard_evidence(
            filter_result.threads
        )
        safe_result = dataclasses.replace(filter_result, threads=safe_threads)
        # Safety exclusions from BOTH passes: the title screen before anything
        # was fetched, and the full-text screen after. One number, because from
        # the reader's side it is one fact — this much evidence was refused.
        unsafe_threads = screen.unsafe + unsafe_full
        if not safe_result.threads:
            return _empty_brief(
                subreddits,
                "Discussions were found and read, but the safety layer "
                "rejected all of them, so none could be used as evidence.",
                EvidenceState.UNSAFE_EVIDENCE,
            )

        # 7. Synthesise (LLM), then apply the deterministic confidence ceiling.
        brief = self._synthesise(
            query, safe_result, subreddits, screen, unsafe_threads, safe_content
        )
        if use_cache:
            research_cache.put(
                cache_key,
                brief.model_dump(mode="json"),
                kind=research_cache.DECISION_CACHE_KIND,
            )
        return brief

    # ---- internals ----

    def _screen_relevance(
        self, query: str, candidates: list[RedditThread]
    ) -> RelevanceScreen:
        """Drop search hits that are clearly about something else.

        One batched LLM call over titles — the same shape as Mode 1's
        retrospective classifier, and cheap next to the thread fetches it
        saves.

        FAILS OPEN, and says so. If the call fails, or the model answers with
        something unusable, every candidate is kept and `screened` is False:
        an LLM outage must not empty a corpus. That honesty has teeth
        downstream — `agents.confidence` RULE 8 caps an unscreened corpus at
        MODERATE, because a relevance check that did not run cannot be claimed
        as passed.

        An explicit empty verdict is different from a failure and is
        respected: the model read the titles and found nothing on-topic, which
        is a real answer to a real question.
        """
        batch = sorted(candidates, key=_engagement_key, reverse=True)[:_RELEVANCE_BATCH]
        # Titles are external content too, and this is the FIRST place they
        # reach a model — earlier than the corpus, and easy to miss for exactly
        # that reason. A title is a string a stranger wrote, and it is about to
        # be read by the model that decides what gets read next.
        batch, lines, unsafe_titles = self._guard_titles(batch)
        if not batch:
            return RelevanceScreen([], screened=True, unsafe=unsafe_titles)

        # Final boundary: the guarded question and the guarded titles were
        # checked separately, and this is the first time they exist as one
        # string. A refusal here is a refusal of the COMBINATION, which no
        # per-piece verdict could have seen.
        user_msg, safe = self._guard.screen_prompt(
            f"Research question: {query}\n\nSearch results:\n{lines}",
            "relevance screen",
        )
        if not safe:
            return RelevanceScreen([], screened=True, unsafe=len(batch))

        try:
            raw = self._llm.complete_json(_RELEVANCE_SYSTEM, user_msg, max_tokens=1500)
        except Exception as exc:  # noqa: BLE001
            logger.warning("relevance screen unavailable: %s", exc)
            return RelevanceScreen(batch, screened=False, unsafe=unsafe_titles)

        ids = raw.get("relevant_ids")
        if not isinstance(ids, list):
            # No verdict at all — not the same thing as "nothing is relevant".
            logger.warning("relevance screen returned no verdict; keeping all candidates")
            return RelevanceScreen(batch, screened=False, unsafe=unsafe_titles)

        # Models sometimes echo ids with the t3_ fullname prefix — normalise,
        # exactly as the retrospective classifier does.
        keep = {str(i).removeprefix("t3_") for i in ids if isinstance(i, (str, int))}
        kept = [t for t in batch if t.id in keep]
        if keep and not kept:
            # It named ids, and not one of them is a candidate we hold. That is
            # an answer about some other set of posts; acting on it would
            # discard a corpus on the strength of a malformed reply.
            logger.warning("relevance screen named no known candidate; keeping all")
            return RelevanceScreen(batch, screened=False, unsafe=unsafe_titles)

        logger.info(
            "relevance screen kept %d of %d candidates", len(kept), len(batch)
        )
        return RelevanceScreen(
            kept,
            screened=True,
            rejected=len(batch) - len(kept),
            unsafe=unsafe_titles,
        )

    def _guard_titles(
        self, batch: list[RedditThread]
    ) -> tuple[list[RedditThread], str, int]:
        """Guard search-result titles before they reach a classifying model.

        Returns (safe threads, the prompt block to send, how many were
        dropped). The block is built here and returned, rather than rebuilt by
        the caller, so what was checked is what gets sent.

        ADAPTIVE, because a batch is up to 60 titles and one guardrail call per
        title would be 60 calls on every research run for a problem that almost
        never occurs. The whole block is checked as one string; only if that
        comes back refused does it re-check title by title, to find which ones
        to drop. Normal runs cost one call; a poisoned run costs the precision
        it needs.

        Masking is honoured either way: it is the returned block that goes into
        the prompt, so personal data in a title never reaches the model.
        """
        from services.bedrock_guardrails import INPUT

        screened = self._guard.screen_batch([_title_line(t) for t in batch], INPUT)
        safe = [batch[i] for i in screened.kept]
        if screened.dropped:
            for candidate in (t for t in batch if t not in safe):
                # Reddit's own thread id only. A subreddit name is chosen by
                # whoever created the community and appears in the very line
                # that was refused, so it is content, not an identifier.
                logger.warning("guardrail rejected candidate %s", candidate.id)
            logger.warning(
                "guardrail excluded %d of %d candidate titles",
                screened.dropped, len(batch),
            )
        return safe, screened.text, screened.dropped

    def _guard_evidence(
        self, threads: list[RedditThread]
    ) -> tuple[list[RedditThread], int, dict[str, str]]:
        """Guard full thread text. Returns (safe, dropped, sanitized by id).

        Each thread is checked as EXACTLY the untrusted text that would be
        rendered into the synthesis prompt — `_thread_content` builds both, so
        what was inspected and what gets sent cannot drift apart.

        source=INPUT, not OUTPUT, and that matters: Bedrock's prompt-attack
        filter only runs on the input side, and prompt injection buried in
        someone else's comment is the whole reason this call exists.

        THE SANITIZED TEXT IS KEPT, not discarded. It is the single sanitized
        copy of this thread's content, and it is what every later consumer
        uses: the synthesis corpus, the embedding input, and the duplicate
        warnings. That is what stops personal data reaching a model through a
        side door — the embedding call runs before the corpus is assembled, so
        anything that re-derived its text from the raw thread would send
        unmasked content to the embedding provider.

        The threads themselves are NEVER mutated. Sanitized text lives in a
        dict keyed by thread id, so ids, counts, ordering and the source list
        shown to the user all stay exactly what they were.

        A guardrail outage raises, because continuing would mean feeding the
        model text nobody checked.
        """
        from services.bedrock_guardrails import INPUT, GuardrailUnavailable

        if not self._guard.enabled or not threads:
            return list(threads), 0, {}

        verdicts = self._guard.check_many(
            [_thread_content(t) for t in threads], INPUT
        )
        if any(v.unavailable for v in verdicts):
            raise GuardrailUnavailable()

        safe: list[RedditThread] = []
        sanitized: dict[str, str] = {}
        for thread, verdict in zip(threads, verdicts):
            if verdict.blocked:
                # Thread id, subreddit and the policy names only. The text that
                # tripped the guardrail is exactly the text that must not be
                # copied into a log file.
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

        dropped = len(threads) - len(safe)
        if dropped:
            logger.warning(
                "guardrail excluded %d of %d threads from the evidence set",
                dropped, len(threads),
            )
        return safe, dropped, sanitized

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
        from services.bedrock_guardrails import GuardrailBlocked

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

        # The query was guarded on its own, but the string the model receives
        # is the query wrapped in a label. Cheap to check (a query is a few
        # hundred characters at most) and it keeps ONE rule true everywhere
        # instead of a rule with an exception: the exact text a model is given
        # has always had its own verdict.
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
        filter_result,
        subreddits: list[str],
        screen: RelevanceScreen,
        unsafe_threads: int = 0,
        content: dict[str, str] | None = None,
    ) -> ResearchBrief:
        from agents.confidence import EvidenceStats, assess
        from agents.evidence import UsableEvidence, verified_claims, verified_prose
        from agents.signal_analysis import analyse_threads

        content = content or {}
        # The sanitized text reaches the duplicate detector too. Embeddings go
        # to a third party BEFORE the prompt is assembled, so passing the
        # already-guarded copy here is what stops masked personal data being
        # sent out through that call instead.
        analysis = analyse_threads(
            filter_result.threads, self._llm, _safe_views(content)
        )
        # THE authoritative evidence set for this brief. Everything the user is
        # shown about the shape of the evidence — the thread count, the
        # communities, the strong-thread count, the confidence ceiling, the
        # source list, and whether a model-authored structural claim is allowed
        # to stand — is derived from this one object below. Nothing recomputes
        # any of it from an earlier, larger set.
        evidence = UsableEvidence.of(analysis.threads)
        threads = evidence.threads
        machine_warnings = analysis.warnings
        # Assembled from the sanitized copy of each thread, so no second,
        # unmasked rendering of the same text ever exists.
        corpus = _build_corpus(threads, content)
        analysis_block = (
            "\n\nAUTOMATED SIGNAL ANALYSIS (machine-generated, factor into "
            "red_flags/confidence):\n- " + "\n- ".join(machine_warnings)
            if machine_warnings
            else ""
        )
        # Final boundary for Mode 2. Everything in this string was screened as
        # a piece — the question, each thread, each duplicate warning — but the
        # pieces have never been weighed together, and this is the string the
        # model actually reads. Checked ONCE and reused by both halves, which
        # send the identical user message.
        #
        # It is also what covers the parts nobody screens individually: the
        # thread headers, which carry externally-chosen subreddit slugs.
        user_msg, safe = self._guard.screen_prompt(
            f"Research query: {query}\n\nThread excerpts:\n\n{corpus}{analysis_block}",
            "research synthesis",
        )
        if not safe:
            # Every thread here passed on its own, so there is no thread to
            # blame and no smaller corpus that is known to be safe. Guessing
            # which one caused a cross-thread interaction would be inventing an
            # answer Bedrock did not give.
            return _empty_brief(
                subreddits,
                "The assembled discussion text was rejected by the safety "
                "layer, so it was not sent for synthesis.",
                EvidenceState.UNSAFE_EVIDENCE,
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
                # Skip the retry rather than send a refused prompt. The brief
                # then has no pick, and the existing rule makes that LOW.
                logger.warning("retry prompt refused; leaving the brief empty")

        # The model's own words, on their way to a screen. Everything the model
        # wrote is checked as OUTPUT before any of it can be displayed or acted
        # on: a brief is not only read, it is the thing `/research/act` spends
        # money against.
        #
        # A blocked string is dropped, not rewritten. If the one dropped is the
        # consensus pick, the brief simply has no recommendation — and the
        # existing rule that a brief without a pick is LOW does the rest,
        # without this code having to reach into the confidence policy.
        raw, blocked_outputs = self._guard.sanitize_model_output(
            raw, _MODEL_AUTHORED_FIELDS, "research synthesis"
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
        represented = list(evidence.represented_subreddits)
        # Counted over the final corpus, so this can never exceed the thread
        # count shown next to it: duplicate collapsing can remove a thread that
        # cleared the bar. The bar itself stays in apply_signal_filters — only
        # the ids of the threads that cleared it travel here.
        strong_in_corpus = evidence.strong_thread_count(filter_result.strong_thread_ids)

        # Structural claims the model wrote about its own evidence are checked
        # against the set above before anyone sees them. This is what stops a
        # brief printing "8 threads across 4 communities" beside a red flag
        # saying "all evidence from a single subreddit". The truthful version
        # of that statement is not lost — when the corpus really is narrow, the
        # confidence policy emits it deterministically as a reason.
        red_flags, flags_removed = verified_claims(raw.get("red_flags", []), evidence)
        bias_notes, notes_removed = verified_prose(raw.get("bias_notes", ""), evidence)

        stats = EvidenceStats(
            usable_thread_count=evidence.thread_count,
            strong_thread_count=strong_in_corpus,
            represented_subreddits=evidence.represented_subreddits,
            filters_relaxed=filter_result.relaxed,
            cross_author_duplicate_count=analysis.cross_author_duplicate_count,
            similarity_analysis_available=analysis.similarity_analysis_available,
            relevance_screened=screen.screened,
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
            red_flags=red_flags,
            confidence=outcome.final,
            semantic_confidence=outcome.semantic,
            structural_ceiling=outcome.ceiling,
            confidence_reasons=list(outcome.reasons),
            signal_quality=SignalQuality(
                subreddits_checked=subreddits,
                subreddits_represented=represented,
                usable_thread_count=evidence.thread_count,
                thread_count=evidence.thread_count,
                strong_thread_count=strong_in_corpus,
                filters_relaxed=filter_result.relaxed,
                coordinated_posting_suspected=analysis.cross_author_duplicate_count > 0,
                duplicate_threads_collapsed=analysis.collapsed_same_author_count,
                similarity_analysis_available=analysis.similarity_analysis_available,
                relevance_screened=screen.screened,
                off_topic_candidates_rejected=screen.rejected,
                unverified_claims_removed=flags_removed + notes_removed,
                unsafe_threads_excluded=unsafe_threads,
                guardrail_blocked_outputs=blocked_outputs,
                evidence_state=EvidenceState.OK,
                date_range=date_range,
                bias_notes=bias_notes,
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
        retro_ids, unsafe_titles = self._classify_retrospectives(decision, candidates)
        retro_candidates = [t for t in candidates if t.id in retro_ids]
        logger.info(
            "classifier kept %d/%d as retrospective", len(retro_candidates), len(candidates)
        )

        thin = len(retro_candidates) < self.THIN_COVERAGE_THRESHOLD
        if not retro_candidates:
            # Two different causes, two different sentences. Blaming the
            # classifier for a safety exclusion would send someone rephrasing a
            # question that was never the problem.
            return _empty_outcomes(
                "Candidate posts were found, but the safety layer rejected "
                "all of them."
                if unsafe_titles and not retro_ids
                else "Candidates found, but none were actual outcome reports "
                "(all prospective questions or irrelevant)."
            )

        # 4. Read the strongest retrospectives in full.
        chosen = sorted(retro_candidates, key=_engagement_key, reverse=True)[:thread_budget]
        full_threads = self._fetch_threads(chosen)
        if not full_threads:
            return _empty_outcomes("Retrospective posts found but none could be fetched.")

        # Same external-content boundary as Mode 2: someone else's text is
        # about to become model context, so each thread is checked and the
        # refused ones are left out.
        full_threads, unsafe, safe_content = self._guard_evidence(full_threads)
        if not full_threads:
            return _empty_outcomes(
                "Retrospective posts were found and read, but the safety layer "
                "rejected all of them."
            )
        if unsafe:
            thin = thin or len(retro_candidates) - unsafe < self.THIN_COVERAGE_THRESHOLD

        # 5. Synthesise outcomes.
        summary = self._synthesise_outcomes(
            decision, full_threads, len(retro_candidates), safe_content
        )
        summary.thin_coverage = thin
        if thin:
            summary.confidence = ConfidenceLevel.LOW
        summary.sources = _to_sources(full_threads)
        return summary

    def _classify_retrospectives(
        self, decision: str, candidates: list[RedditThread]
    ) -> tuple[set[str], int]:
        """One batched LLM call: which candidate titles are genuine
        retrospective outcome reports (not prospective questions)?

        Returns the ids kept and how many candidates the guardrail refused —
        the caller needs the second number to explain an empty result
        truthfully rather than blaming the classifier for it.
        """
        # Cap the batch at 60 titles — but rank by engagement first, so the
        # cut drops the weakest candidates instead of whichever subreddit
        # happened to be searched last.
        batch = sorted(candidates, key=_engagement_key, reverse=True)[:60]
        # Same boundary as Mode 2's relevance screen: these titles are about to
        # be read by a model.
        batch, lines, unsafe = self._guard_titles(batch)
        if not batch:
            return set(), unsafe
        # Final boundary: guarded decision + guarded titles, as one string.
        user_msg, safe = self._guard.screen_prompt(
            f"Decision being researched: {decision}\n\nCandidate posts:\n{lines}",
            "retrospective classifier",
        )
        if not safe:
            return set(), len(batch)
        raw = self._llm.complete_json(_CLASSIFY_SYSTEM, user_msg, max_tokens=1500)
        ids = raw.get("retrospective_ids", [])
        # Models sometimes echo ids with the t3_ fullname prefix — normalise.
        return {
            str(i).removeprefix("t3_") for i in ids if isinstance(i, (str, int))
        }, unsafe

    def _synthesise_outcomes(
        self,
        decision: str,
        threads: list[RedditThread],
        retro_count: int,
        content: dict[str, str] | None = None,
    ) -> OutcomeSummary:
        from agents.signal_analysis import analyse_threads

        content = content or {}

        # Mode 1 is unchanged by the Phase A structural work: it reads the
        # prose half of the analysis exactly as before. Its confidence already
        # has a deterministic floor of its own (`thin_coverage` in
        # `mine_retrospectives`), and widening the structural policy to
        # retrospectives is deliberately out of scope here.
        analysis = analyse_threads(threads, self._llm, _safe_views(content))
        threads = analysis.threads
        machine_warnings = analysis.warnings
        corpus = _build_corpus(threads, content)
        analysis_block = (
            "\n\nAUTOMATED SIGNAL ANALYSIS (machine-generated, factor into "
            "sample_bias/confidence):\n- " + "\n- ".join(machine_warnings)
            if machine_warnings
            else ""
        )
        # Final boundary for Mode 1, same reasoning as Mode 2.
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
        # The model's words, guarded before they reach a screen.
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


def _title_line(t: RedditThread) -> str:
    """One search hit as a classifying model sees it.

    Defined once and used to build the prompt AND to guard it, so the text
    that was inspected and the text that is sent cannot drift apart.
    """
    return f"{t.id} | r/{t.subreddit} | {t.title[:120]}"


def _thread_content(t: RedditThread) -> str:
    """The UNTRUSTED part of one thread: everything a stranger wrote.

    Split out from the header on purpose. This is the text that gets guarded,
    the text that gets masked, and — once masked — the text that is embedded
    and the text that goes into the prompt. One string per thread, sanitized
    once, reused everywhere, so no consumer can quietly re-derive an unmasked
    version from the raw thread.

    The header is built separately because it carries no free prose — a
    subreddit slug, two numbers and a month — so screening it per thread would
    spend guardrail characters on almost nothing. That is a judgement about
    cost, NOT a claim that it is our own text: a subreddit name is chosen by
    whoever created the community, and Reddit only constrains its format. It is
    covered where it actually matters, in the final assembled-prompt check that
    every synthesis call goes through.
    """
    lines = [f"TITLE: {t.title}"]
    if t.body:
        lines.append(f"POST: {t.body[:1200]}")
    for c in t.top_comments[:10]:
        lines.append(f"COMMENT: {c[:600]}")
    return "\n".join(lines)


def _thread_header(t: RedditThread, index: int) -> str:
    """The metadata line for one thread: position, community, engagement, date.

    Only the subreddit slug comes from outside, and it reaches the model inside
    the final assembled-prompt check rather than through a call of its own.
    """
    from datetime import datetime, timezone

    when = (
        datetime.fromtimestamp(t.created_utc, tz=timezone.utc).strftime("%Y-%m")
        if t.created_utc
        else "?"
    )
    return (
        f"--- Thread {index} | r/{t.subreddit} | {t.score} pts | "
        f"{t.num_comments} comments | {when} ---"
    )


def _content_parts(content: str) -> tuple[str, str]:
    """(title, post) read back out of a rendered content block.

    Reading back a block this module generated is exact rather than
    approximate: the prefixes are ours, and Reddit's own parser collapses
    newlines out of titles, bodies and comments before they ever get here, so
    a value can never span two lines.
    """
    title = ""
    post = ""
    for line in content.split("\n"):
        if not title and line.startswith("TITLE: "):
            title = line[len("TITLE: "):]
        elif not post and line.startswith("POST: "):
            post = line[len("POST: "):]
    return title, post


def _embedding_text(content: str) -> str:
    """The duplicate detector's input, derived from a content block.

    It has always embedded `title + first 600 characters of body`, and it still
    does — the only difference is that the text can now be the SANITIZED copy.
    `test_the_embedding_input_is_unchanged_apart_from_masking` pins that the
    formula is byte-identical to the old one on unsanitized input.
    """
    title, post = _content_parts(content)
    return f"{title}\n{post[:600]}"


def _safe_views(content: dict[str, str]) -> dict:
    """Sanitized per-thread strings for `analyse_threads`, keyed by thread id.

    Derived from the ONE sanitized copy of each thread's text, so the embedding
    call and the synthesis prompt cannot end up seeing different versions of
    the same thread.
    """
    from agents.signal_analysis import SafeThreadText

    return {
        tid: SafeThreadText(
            title=_content_parts(text)[0], embed=_embedding_text(text)
        )
        for tid, text in content.items()
    }


def _build_corpus(
    threads: Sequence[RedditThread], content: dict[str, str] | None = None
) -> str:
    """Rendered thread excerpts for the synthesis prompt.

    `content` supplies the guardrail-sanitized text for a thread, keyed by id.
    A thread with no entry falls back to its raw content, which is the correct
    behaviour when guardrails are switched off — there is nothing to sanitize.
    """
    safe = content or {}
    return "\n\n".join(
        f"{_thread_header(t, i)}\n{safe.get(t.id) or _thread_content(t)}"
        for i, t in enumerate(threads, 1)
    )


def _to_sources(threads: Sequence[RedditThread]) -> list[SourceThread]:
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
