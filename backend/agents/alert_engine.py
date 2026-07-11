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
  "issue_tag": "short-kebab-case tag for the dominant issue, or empty string"
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
- Posts marked [score hidden] are recent; judge by content."""

_LEVEL_ORDER = {
    SignalLevel.NONE: 0,
    SignalLevel.LOW: 1,
    SignalLevel.MEDIUM: 2,
    SignalLevel.HIGH: 3,
}


class AlertEngine:
    def __init__(self, llm: LLMService | None = None) -> None:
        self._llm = llm or LLMService()

    # ---- stage 1: absolute classification of this run ----

    def classify_run(self, item_name: str, posts: list[RedditThread]) -> dict:
        """LLM read of this run's posts. Returns the raw_findings dict stored
        on the run row: sentiment/signal_level/summary/notable_urls/issue_tag."""
        if not posts:
            return {
                "sentiment": "neutral",
                "signal_level": "none",
                "summary": "No recent posts mentioning this item.",
                "notable_urls": [],
                "issue_tag": "",
            }
        lines = "\n".join(
            f"{t.id} | r/{t.subreddit} | {t.title[:140]}" for t in posts[:40]
        )
        raw = self._llm.complete_json(
            _CLASSIFY_RUN_SYSTEM,
            f"Monitored item: {item_name}\n\nRecent posts (past week):\n{lines}",
            max_tokens=800,
        )
        by_id = {t.id: t for t in posts}
        notable_urls = [
            f"https://www.reddit.com/comments/{str(i).removeprefix('t3_')}/"
            for i in raw.get("notable_thread_ids", [])
            if str(i).removeprefix("t3_") in by_id
        ]
        level = str(raw.get("signal_level", "none")).lower()
        if level not in ("none", "low", "medium", "high"):
            level = "none"
        return {
            "sentiment": str(raw.get("sentiment", "neutral")).lower(),
            "signal_level": level,
            "summary": raw.get("summary", ""),
            "notable_urls": notable_urls,
            "issue_tag": raw.get("issue_tag", "") or "",
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

        return MonitorAlert(
            item_id=item_id,
            run_id=run_id,
            severity=severity,
            summary=summary,
            thread_urls=findings.get("notable_urls", []),
            recommended_action=ActionType.NONE,  # Phase 4 wires mandates
        )
