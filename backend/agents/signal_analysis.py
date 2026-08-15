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
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass

from core.logging import get_logger
from core.models import RedditThread
from services.llm import LLMService

logger = get_logger(__name__)

# Cosine thresholds for 1536-dim text-embedding-3-small.
_NEAR_DUP = 0.90


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    return dot / (na * nb) if na and nb else 0.0


@dataclass(frozen=True)
class SafeThreadText:
    """Guardrail-sanitized text for one thread, prepared by the caller.

    This module does not know how a thread is rendered for a prompt and does
    not want to. The caller has already had Bedrock check (and possibly mask)
    each thread's content, so it hands over the two derived strings this module
    actually uses: what to embed, and what to call a thread in a warning.

    WHY IT EXISTS: embeddings are sent to a third party BEFORE the synthesis
    prompt is assembled and masked. Anything that rebuilt its own text from the
    raw thread here would ship unmasked personal data to the embedding
    endpoint even though the guardrail had already removed it.
    """

    title: str
    embed: str


def analyse_threads(
    threads: list[RedditThread],
    llm: LLMService | None = None,
    safe_text: dict[str, SafeThreadText] | None = None,
) -> tuple[list[RedditThread], list[str]]:
    """Returns (possibly reduced thread list, machine warnings for the
    synthesis prompt). Never raises.

    `safe_text` maps thread id to its guardrail-sanitized strings. Absent —
    guardrails switched off — the raw thread is used and the behaviour is
    exactly what it was.

    The thread objects themselves are never modified: ids, provenance and the
    source list shown to the user stay as they were.
    """
    warnings: list[str] = []
    if len(threads) < 2:
        return threads, _coverage_note(threads)

    llm = llm or LLMService()
    views = [_view(t, safe_text) for t in threads]
    try:
        vectors = llm.embed([v.embed for v in views])
    except Exception as exc:  # noqa: BLE001
        logger.warning("embedding analysis skipped: %s", exc)
        return threads, _coverage_note(threads)

    _persist_embeddings(threads, vectors)

    drop: set[str] = set()
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
                # Titles come from the sanitized view: this warning is appended
                # to the same prompt as the corpus and would otherwise be a
                # second, unmasked copy of the same external text.
                #
                # The usernames are gone deliberately. They are personal data
                # about real people, and the model does not need them: what it
                # has to know is that the authors DIFFER, not who they are.
                warnings.append(
                    f"Threads titled {views[i].title[:60]!r} (r/{a.subreddit}) "
                    f"and {views[j].title[:60]!r} (r/{b.subreddit}) are "
                    f"near-identical content ({sim:.0%} similar) from DIFFERENT "
                    "authors — treat them as ONE source and consider "
                    "coordinated posting; weight any product they push "
                    "accordingly."
                )

    kept = [t for t in threads if t.id not in drop]
    return kept, warnings + _coverage_note(kept)


def _view(
    t: RedditThread, safe_text: dict[str, SafeThreadText] | None
) -> SafeThreadText:
    """The sanitized view of a thread, or the raw one when none was prepared.

    The fallback is the pre-guardrail formula, unchanged, so a corpus that was
    never guarded embeds exactly what it always did.
    """
    prepared = (safe_text or {}).get(t.id)
    if prepared is not None:
        return prepared
    return SafeThreadText(title=t.title, embed=f"{t.title}\n{t.body[:600]}")


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
