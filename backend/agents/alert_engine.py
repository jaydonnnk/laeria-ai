"""Alert engine — decides when monitored-item signal warrants an alert.

Core rule: alert on CHANGE against a historical baseline, not on absolute
sentiment. A service that is always mildly complained about should not
generate a permanent high alert. Phase 3.
"""

from __future__ import annotations

from core.logging import get_logger
from core.models import MonitorAlert, MonitoredItem, RedditThread

logger = get_logger(__name__)


class AlertEngine:
    SIGNAL_THRESHOLDS = {
        "low": 1,     # surface only
        "medium": 3,  # notify
        "high": 5,    # act, if mandate allows
    }

    def evaluate(
        self,
        item: MonitoredItem,
        new_findings: list[RedditThread],
        history: list[dict],
    ) -> MonitorAlert | None:
        """Compare new findings against baseline; emit an alert or None."""
        raise NotImplementedError("Phase 3: change-based alert evaluation")
