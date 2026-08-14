"""Structural anti-shill analysis (Phase 5 / WARP defenses).

Coordinated Reddit campaigns leave a fingerprint the LLM can't reliably spot
from prose alone: the same content posted across subreddits. This module
detects it with embeddings BEFORE synthesis:

- embed each thread (title + body head) via OpenRouter
- pairwise cosine similarity
- same author + near-identical -> self-crosspost: collapse to the best copy
  (mild signal; people legitimately crosspost their own question)
- DIFFERENT authors + near-identical -> coordinated-posting suspicion:
  keep the threads but hand the synthesis an explicit machine-generated
  warning it must weigh

Also emits a coverage line (threads per subreddit) so the synthesis can
apply cross-subreddit corroboration structurally, and persists embeddings to
the thread_embeddings table (best-effort) for future cross-session reuse.

Account-age filtering remains impossible via HTML scraping (one extra
request per author); it returns when a provider replaces scraping.

Everything here is best-effort: if embeddings fail, research proceeds
without the analysis rather than dying.

What this module finds is returned as DATA as well as prose. The warnings
below still go into the synthesis prompt exactly as before — the model needs
them to write honest `red_flags` — but the counts now also reach
`agents.confidence`, which can act on them. Prose alone could only ask the
model to "cap confidence accordingly"; the structured half lets the pipeline
insist. Both paths are wanted, and neither replaces the other.
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field

from core.logging import get_logger
from core.models import RedditThread
from services.llm import LLMService

logger = get_logger(__name__)

# Cosine thresholds for 1536-dim text-embedding-3-small.
_NEAR_DUP = 0.90


@dataclass(frozen=True)
class SignalAnalysisResult:
    """Structural findings for one corpus: the prose and the counts behind it."""

    threads: list[RedditThread]
    # Machine-generated warnings for the synthesis prompt. Unchanged in
    # content and ordering from before this struct existed.
    warnings: list[str] = field(default_factory=list)
    collapsed_same_author_count: int = 0
    cross_author_duplicate_count: int = 0
    # Whether the duplicate verdict for this corpus can be trusted. False means
    # the detector could not run (embeddings failed), NOT that it ran and found
    # nothing — a distinction the confidence policy depends on.
    similarity_analysis_available: bool = False


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    return dot / (na * nb) if na and nb else 0.0


def analyse_threads(
    threads: list[RedditThread], llm: LLMService | None = None
) -> SignalAnalysisResult:
    """Structural analysis of a corpus. Never raises.

    Returns the (possibly reduced) thread list, the machine warnings for the
    synthesis prompt, and the counts behind them.
    """
    warnings: list[str] = []
    if len(threads) < 2:
        # Nothing to compare, so there is no pair the detector could have
        # missed: the "no near-duplicates" verdict is trivially correct rather
        # than unverified, and is reported as available. A corpus this small
        # is capped at LOW by the thread-count rule regardless.
        return SignalAnalysisResult(
            threads=threads,
            warnings=_coverage_note(threads),
            similarity_analysis_available=True,
        )

    llm = llm or LLMService()
    try:
        texts = [f"{t.title}\n{t.body[:600]}" for t in threads]
        vectors = llm.embed(texts)
    except Exception as exc:  # noqa: BLE001
        logger.warning("embedding analysis skipped: %s", exc)
        # Research proceeds, but the caller is told the check did not run —
        # otherwise an embeddings outage would silently look identical to a
        # corpus that was actually verified as independent.
        return SignalAnalysisResult(
            threads=threads,
            warnings=_coverage_note(threads),
            similarity_analysis_available=False,
        )

    _persist_embeddings(threads, vectors)

    drop: set[str] = set()
    cross_author_pairs = 0
    for i in range(len(threads)):
        for j in range(i + 1, len(threads)):
            a, b = threads[i], threads[j]
            if a.id in drop or b.id in drop:
                continue
            sim = _cosine(vectors[i], vectors[j])
            if sim < _NEAR_DUP:
                continue
            same_author = a.author and a.author == b.author
            if same_author:
                # Self-crosspost: collapse to the higher-engagement copy.
                loser = b if (a.score + a.num_comments) >= (b.score + b.num_comments) else a
                drop.add(loser.id)
                logger.info("collapsed self-crosspost %s (dup of %s)", loser.id, a.id)
            else:
                cross_author_pairs += 1
                warnings.append(
                    f"Threads titled {a.title[:60]!r} (r/{a.subreddit}, u/{a.author}) "
                    f"and {b.title[:60]!r} (r/{b.subreddit}, u/{b.author}) are "
                    f"near-identical content ({sim:.0%} similar) from DIFFERENT "
                    "authors — treat them as ONE source and consider "
                    "coordinated posting; weight any product they push "
                    "accordingly."
                )

    kept = [t for t in threads if t.id not in drop]
    return SignalAnalysisResult(
        threads=kept,
        warnings=warnings + _coverage_note(kept),
        collapsed_same_author_count=len(drop),
        cross_author_duplicate_count=cross_author_pairs,
        similarity_analysis_available=True,
    )


def _coverage_note(threads: list[RedditThread]) -> list[str]:
    if not threads:
        return []
    per_sub = Counter(t.subreddit for t in threads)
    spread = ", ".join(f"r/{s}: {n}" for s, n in per_sub.most_common())
    note = f"Coverage: {len(threads)} threads across {len(per_sub)} subreddit(s) ({spread})."
    if len(per_sub) == 1:
        note += (
            " All evidence comes from a SINGLE subreddit — no cross-community "
            "corroboration is possible; cap confidence accordingly."
        )
    return [note]


def _persist_embeddings(threads: list[RedditThread], vectors: list[list[float]]) -> None:
    """Store embeddings for future cross-session dedup. Best-effort."""
    try:
        from db.client import get_supabase

        rows = [
            {"thread_id": t.id, "subreddit": t.subreddit, "embedding": v}
            for t, v in zip(threads, vectors)
        ]
        get_supabase().table("thread_embeddings").insert(rows).execute()
    except Exception as exc:  # noqa: BLE001
        logger.debug("embedding persistence skipped: %s", exc)
