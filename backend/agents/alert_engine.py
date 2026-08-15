"""Alert engine — decides when monitored-item signal warrants an alert.

Core rule: alert on CHANGE against a historical baseline, not on absolute
sentiment. A service that is always mildly complained about should not
generate a permanent high alert.

Two stages per run:
1. classify_run (LLM): recent posts -> {sentiment, signal_level, summary,
   notable thread urls}. Absolute read of this run only.
2. evaluate (pure logic): this run vs the recent-runs baseline -> alert or
   None. Escalates on deterioration and persistence, stays quiet on a noisy
   but stable baseline.
"""

from __future__ import annotations

from core.logging import get_logger
from core.models import (
    ActionType,
    MonitorAlert,
    RedditThread,
    SignalLevel,
)
from services.llm import LLMService

logger = get_logger(__name__)

_CLASSIFY_RUN_SYSTEM = """You monitor Reddit for signal about a product or \
service the user owns or subscribes to. Given recent posts, respond with JSON \
only:

{
  "sentiment": "positive" | "neutral" | "negative",
  "signal_level": "none" | "low" | "medium" | "high",
  "summary": "one or two sentences: what is being said about this item right now",
  "notable_thread_ids": ["ids of posts that carry the signal"],
  "issue_tag": "short-kebab-case tag for the dominant issue, or empty string",
  "recommended_action": "none" | "cancel_subscription" | "order_replacement"
}

signal_level meaning (calibrate strictly):
- "none": routine chatter, questions, purchases, praise. The default.
- "low": scattered complaints with no pattern, or a single notable report.
- "medium": multiple independent posts reporting the SAME problem this week
  (defect, outage, quality drop, price hike, service shutdown rumor).
- "high": widespread consistent reports of a serious problem: recall,
  bricking update, data breach, confirmed shutdown, dangerous defect.

Rules:
- Judge only from the provided posts. Routine complaint noise exists for
  every product — that is "none" or "low", not "medium".
- issue_tag names the pattern ("app-update-broken", "price-increase") so
  runs can be compared; keep it stable and generic.
- Posts marked [score hidden] are recent; judge by content.
- recommended_action: "cancel_subscription" only for a SERVICE the user pays
  recurring money for AND the signal is medium/high (shutdown, sustained
  quality collapse, price hike). "order_replacement" only for a PHYSICAL
  product with a defect/recall pattern at medium/high. Otherwise "none".
  This is a recommendation the user must approve — still be conservative."""

_LEVEL_ORDER = {
    SignalLevel.NONE: 0,
    SignalLevel.LOW: 1,
    SignalLevel.MEDIUM: 2,
    SignalLevel.HIGH: 3,
}


def _quiet(summary: str) -> dict:
    """A run that reports nothing.

    `signal_level: none` is what `evaluate` needs to see to raise no alert, and
    `recommended_action: none` stops an action being proposed. Both are stated
    rather than left to a default: this shape is returned on every path where
    the engine declines to judge, and a missing key on one of them would be the
    bug that matters.
    """
    return {
        "sentiment": "neutral",
        "signal_level": "none",
        "summary": summary,
        "notable_urls": [],
        "issue_tag": "",
        "recommended_action": "none",
    }


class AlertEngine:
    def __init__(
        self,
        llm: LLMService | None = None,
        guardrails=None,  # noqa: ANN001
    ) -> None:
        from services.bedrock_guardrails import get_guardrails

        self._llm = llm or LLMService()
        # Injectable so no test needs AWS. Defaults to the shared instance,
        # which is a clean no-op unless BEDROCK_GUARDRAILS_ENABLED is set.
        self._guard = guardrails if guardrails is not None else get_guardrails()

    # ---- stage 1: absolute classification of this run ----

    def classify_run(self, item_name: str, posts: list[RedditThread]) -> dict:
        """LLM read of this run's posts. Returns the raw_findings dict stored
        on the run row: sentiment/signal_level/summary/notable_urls/issue_tag.

        Guarded on both sides. The item name and the post titles are checked
        before they enter a prompt, the assembled prompt is checked as one
        string, and the model's answer is checked before it can become
        findings — because on this path findings become alerts and an alert can
        carry a recommended action.

        Stage 2 (`evaluate`) is untouched: still pure change-detection logic
        with no network, no model and no guardrail in it.
        """
        from services.bedrock_guardrails import INPUT

        if not posts:
            return _quiet("No recent posts mentioning this item.")

        # The item name is user-controlled text that enters this prompt on
        # every run. Checked HERE, at the moment it would reach a model — the
        # creation-time check in the monitor route is an early rejection, not
        # the boundary, because rows predating this integration, seeded items
        # and internal callers never pass through it.
        item_name = self._guard.ensure_allowed(
            item_name, INPUT, "monitored item name"
        )

        batch = posts[:40]
        screened = self._guard.screen_batch(
            [f"{t.id} | r/{t.subreddit} | {t.title[:140]}" for t in batch], INPUT
        )
        safe_posts = [batch[i] for i in screened.kept]
        if screened.dropped:
            logger.warning(
                "guardrail excluded %d of %d monitored posts before classification",
                screened.dropped, len(batch),
            )
        if not safe_posts:
            return _quiet(
                "Recent posts were found, but the safety layer refused all of "
                "them, so this check reports no signal."
            )

        user_msg, safe = self._guard.screen_prompt(
            f"Monitored item: {item_name}\n\nRecent posts (past week):\n"
            f"{screened.text}",
            "monitor classification",
        )
        if not safe:
            return _quiet(
                "The assembled check was refused by the safety layer, so no "
                "signal is reported for this run."
            )

        raw = self._llm.complete_json(_CLASSIFY_RUN_SYSTEM, user_msg, max_tokens=800)
        # The model's own words, before they can become an alert or an action.
        # A blocked summary means a QUIET run, not a partial alert: the summary
        # is what a human reads and what the proposed action is described by.
        raw, blocked = self._guard.sanitize_model_output(
            raw, ("summary", "issue_tag"), "monitor classification"
        )
        if blocked:
            logger.warning(
                "guardrail refused the monitor summary — reporting no signal"
            )
            return _quiet(
                "The safety layer refused this check's summary, so no signal "
                "is reported for this run."
            )

        # Built from the SAFE posts only, so a refused post's URL can never be
        # surfaced as notable evidence.
        by_id = {t.id: t for t in safe_posts}
        notable_urls = [
            f"https://www.reddit.com/comments/{str(i).removeprefix('t3_')}/"
            for i in raw.get("notable_thread_ids", [])
            if str(i).removeprefix("t3_") in by_id
        ]
        level = str(raw.get("signal_level", "none")).lower()
        if level not in ("none", "low", "medium", "high"):
            level = "none"
        rec = str(raw.get("recommended_action", "none")).lower()
        if rec not in ("none", "cancel_subscription", "order_replacement"):
            rec = "none"
        return {
            "sentiment": str(raw.get("sentiment", "neutral")).lower(),
            "signal_level": level,
            "summary": raw.get("summary", ""),
            "notable_urls": notable_urls,
            "issue_tag": raw.get("issue_tag", "") or "",
            "recommended_action": rec,
        }

    # ---- stage 2: change vs baseline ----

    def evaluate(
        self,
        item_id: str,
        run_id: str | None,
        findings: dict,
        history: list[dict],
    ) -> MonitorAlert | None:
        """Compare this run's findings against the item's recent runs.

        history: recent monitor_runs rows (newest first), NOT including this
        run. Alert logic:
        - high signal -> always alert (severity high).
        - medium signal -> alert if the baseline was quiet (mostly none/low),
          or escalate to high if the same issue_tag persisted last run too.
        - low/none -> no alert. Chronic mediums with an unchanged issue_tag
          don't re-alert every run — only when the issue is new or worsens.
        """
        level = SignalLevel(findings["signal_level"])
        if _LEVEL_ORDER[level] < _LEVEL_ORDER[SignalLevel.MEDIUM]:
            return None

        prev = history[0] if history else None
        prev_tag = (prev.get("raw_findings") or {}).get("issue_tag", "") if prev else ""
        prev_level = prev.get("signal_level", "none") if prev else "none"
        baseline_levels = [r.get("signal_level", "none") for r in history]
        baseline_quiet = all(lvl in ("none", "low") for lvl in baseline_levels)

        severity = level
        tag = findings.get("issue_tag", "")

        if level == SignalLevel.MEDIUM:
            same_issue_persisting = tag and tag == prev_tag and prev_level in ("medium", "high")
            if same_issue_persisting:
                # Second consecutive run with the same medium issue -> high.
                severity = SignalLevel.HIGH
            elif not baseline_quiet:
                # Noisy baseline, another medium: not a change, stay quiet.
                logger.info(
                    "item %s: medium signal on noisy baseline (%s) — no alert",
                    item_id, baseline_levels,
                )
                return None

        summary = findings.get("summary", "")
        if severity == SignalLevel.HIGH and level == SignalLevel.MEDIUM:
            summary = f"Persisting across checks: {summary}"

        try:
            recommended = ActionType(findings.get("recommended_action", "none"))
        except ValueError:
            recommended = ActionType.NONE

        return MonitorAlert(
            item_id=item_id,
            run_id=run_id,
            severity=severity,
            summary=summary,
            thread_urls=findings.get("notable_urls", []),
            recommended_action=recommended,
        )
