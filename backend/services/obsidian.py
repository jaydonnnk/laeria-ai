"""Obsidian vault access via the Local REST API community plugin.

Used in Phase 3 to read what the user owns/subscribes to (to infer monitored
items) and to write alerts and action logs back into the vault.

The plugin serves HTTPS on 127.0.0.1:27124 with a self-signed cert, so SSL
verification is disabled by default (see OBSIDIAN_VERIFY_SSL).
"""

from __future__ import annotations

import httpx

from core.config import get_settings
from core.logging import get_logger
from core.models import MonitoredItem

logger = get_logger(__name__)


class ObsidianService:
    def __init__(self) -> None:
        settings = get_settings()
        self._base_url = settings.obsidian_api_url
        self._headers = {"Authorization": f"Bearer {settings.obsidian_api_key}"}
        self._verify = settings.obsidian_verify_ssl

    def healthcheck(self) -> bool:
        """Ping the vault. Non-fatal if the vault isn't open — Obsidian is
        optional for Modes 1 and 2. test_environment reports it as a warning,
        not a failure."""
        try:
            resp = httpx.get(
                f"{self._base_url}/",
                headers=self._headers,
                verify=self._verify,
                timeout=5.0,
            )
            return resp.status_code == 200
        except Exception as exc:  # noqa: BLE001
            logger.warning("Obsidian healthcheck failed (vault may be closed): %s", exc)
            return False

    # ---- Phase 3 ----

    def get_all_notes(self) -> list[dict]:
        raise NotImplementedError("Phase 3: list vault notes")

    def search_notes(self, query: str) -> list[dict]:
        raise NotImplementedError("Phase 3: search vault")

    def extract_monitored_items(self) -> list[MonitoredItem]:
        """LLM reads vault, infers what the user owns/subscribes to.
        User must approve before monitoring starts."""
        raise NotImplementedError("Phase 3: infer monitored items from vault")

    def write_alert_note(self, alert_summary: str) -> None:
        raise NotImplementedError("Phase 3: write alert back to vault")

    def write_action_log(self, action_summary: str) -> None:
        raise NotImplementedError("Phase 3: append to action log note")
