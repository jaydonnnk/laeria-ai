"""How well does a candidate thread answer THIS question?

Mode 2 searches Reddit, gets back far more candidates than it can afford to
read in full, and has to choose. Until this module existed the choice was made
on engagement alone — `score + num_comments` — which answers "is this thread
popular?" and never asks "is this thread about the thing that was asked?".

Measured against live Reddit, that is not a small gap. For "Is the Koss KSC75
worth buying under $30?" the eight threads engagement picked were led by "Half a
year sober from the hobby" (486 pts) and "I need to stop buying headphones!"
(228 pts), while "Is the Koss KSC75 Worth Buying?" (1 pt) and "Are koss ksc 75
worth it?" (5 pts) sat unread in the same candidate pool. Reddit search is
already asked for `sort=relevance`, so the ordering that would have caught this
was fetched, paid for, and then thrown away by a global engagement sort.

The scoring is deliberately plain — no model call, no embedding, no network.
That is not only about latency and cost: a new model call would be a new place
where untrusted Reddit text reaches a third party, and would need its own
guardrail boundary. Counting words in a title that is already in memory adds no
trust boundary at all, so the existing ones keep their meaning.

TWO PROPERTIES MAKE IT HARD TO GAME:

* Each query term counts ONCE (set semantics). A title repeating "ksc75 ksc75
  ksc75" scores exactly the same as one that says it plainly, so keyword
  stuffing buys nothing.
* Term weight is inverse document frequency over the candidate set itself, so a
  term is worth less the more candidates contain it. Flooding the pool with one
  keyword drives that keyword's own weight down.

IDF over the candidates is also what keeps this honest without a hand-tuned
weight table: for a KSC75 query "ksc75" is rare among candidates and dominates,
while "headphones" is nearly everywhere and barely registers. Nothing external
is consulted and no threshold is guessed at.
"""

from __future__ import annotations

import math
import re

from core.models import RedditThread

# Words that describe the SHAPE of a question rather than its subject.
#
# This is not a general stopword list and should not grow into one. Everything
# here was earning score for the wrong reason on real queries: "worth" made
# "is a Logitech z523 worth it at only 30€?" a top hit for a KSC75 question,
# "review" pulled in "Moondrop Old Fashioned Review", and "best" made "Best AA
# batteries for wireless mouse??" outrank a 61-battery comparison table.
#
# Attribute words a buyer actually means — "cheap", "budget", "wireless" — are
# deliberately NOT here. They narrow the topic rather than framing the ask.
_STOPWORDS = frozenset("""
a an the is are am was were be been being do does did doing done
i me my mine you your yours it its this that these those they them their we us
and or but if then so than as at by for from in into of on onto to with
what which who whom whose when where why how whether
should would could can will shall may might must just really very much
worth buying buy bought purchase purchasing get getting got own owning
recommend recommendations recommendation recommended suggest suggestions
help need needed looking look advice thoughts opinion opinions experience
review reviews reviewing comparison compare vs versus guide
any some all more most other others new old good great
best top favourite favorite ideal perfect ultimate greatest
under over below above around about max maximum least less cheaper price
""".split())

_TOKEN = re.compile(r"[a-z0-9]+")

# What the user typed outranks what the planner guessed. The planner's phrases
# are a useful expansion — they contribute community vocabulary the user never
# typed, like "eneloop" for a rechargeable-battery question — but they are an
# inference about the question, not the question. Without this, "budget" (a
# planner word) outweighed "batteries" (the user's word) and a flashlight
# thread beat a battery comparison.
_USER_TERM_WEIGHT = 2.0


def _tokens(text: str) -> list[str]:
    return _TOKEN.findall(text.lower())


def topic_terms(text: str) -> set[str]:
    """Subject terms in a query or search phrase.

    Bare integers of three digits or fewer are dropped: in "best cheap keyboard
    under $30" the 30 is the budget, and matching it lexically rewarded "is a
    Logitech z523 or x530 worth it at only 30€?". Longer bare numbers survive
    because they are model/format names people really search — 18650 cells,
    1080 cards.
    """
    terms = set()
    for tok in _tokens(text):
        if len(tok) < 2 or tok in _STOPWORDS:
            continue
        if tok.isdigit() and len(tok) <= 3:
            continue
        terms.add(tok)
    return terms


def title_terms(title: str) -> set[str]:
    """Matchable terms in a candidate title.

    Adjacent letter-then-digit tokens are ALSO emitted glued together, so a
    thread titled "Are koss ksc 75 worth it?" matches a query that says
    "KSC75". Reddit titles space model numbers however the poster felt that
    day, and an exact-model query is exactly where a miss costs the most.

    No stopword filtering here: the query side already decided what is worth
    matching, and filtering both sides would only cost time.
    """
    toks = _tokens(title)
    terms = {t for t in toks if len(t) >= 2}
    terms.update(a + b for a, b in zip(toks, toks[1:]) if a.isalpha() and b.isdigit())
    return terms


def build_weights(
    query: str, search_queries: list[str], candidates: list[RedditThread]
) -> dict[str, float]:
    """Per-term scoring weights for one research run.

    Weight is IDF over the candidate titles, doubled for terms the user
    actually typed. Always positive — a term match can never lower a score —
    so this only ever reorders, it cannot exclude a thread the old code kept.
    """
    user = topic_terms(query)
    planner = set().union(*(topic_terms(q) for q in search_queries)) if search_queries else set()

    docs = [_matchable(t) for t in candidates]
    n = len(docs) or 1
    weights = {}
    for term in user | planner:
        df = sum(1 for d in docs if term in d)
        idf = math.log(n / (1 + df)) + 1.0
        weights[term] = idf * (_USER_TERM_WEIGHT if term in user else 1.0)
    return weights


# How much body text a thread may contribute to matching. A search snippet is
# a couple of sentences; this is generous enough to hold one and short enough
# that a long selftext cannot swamp the title it belongs to.
_BODY_MATCH_CHARS = 400


def _matchable(thread: RedditThread) -> set[str]:
    """Terms a thread can be matched on: its title, plus the head of its body.

    Title alone was right while the only source was a Reddit search page, which
    never populates `body`. A discovery provider returns a snippet instead — it
    IS the body we hold — and a preview whose title is "Anyone tried these?"
    matches nothing at all unless the snippet is read.

    Reading the head of the body is therefore free on the existing paths (there
    `body` is empty at selection time, so this is byte-for-byte the old
    behaviour) and is the difference between ranking and guessing on the new
    one. Bounded because relevance must stay a judgement about the subject, not
    a reward for writing at length.
    """
    terms = title_terms(thread.title)
    if thread.body:
        terms |= title_terms(thread.body[:_BODY_MATCH_CHARS])
    return terms


def score(thread: RedditThread, weights: dict[str, float]) -> float:
    """Total weight of the DISTINCT query terms this thread's text contains.

    Distinct on purpose: a term counts once whether it appears in the title, in
    the snippet, or ten times in both, so padding a post cannot buy rank.
    """
    if not weights:
        return 0.0
    have = _matchable(thread)
    return sum(w for term, w in weights.items() if term in have)
