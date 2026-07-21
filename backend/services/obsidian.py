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

    def _list_files(self, path: str = "") -> list[str]:
        """Recursively list markdown files. The REST API returns directory
        entries ending in '/'."""
        resp = httpx.get(
            f"{self._base_url}/vault/{path}",
            headers=self._headers,
            verify=self._verify,
            timeout=10.0,
        )
        resp.raise_for_status()
        files: list[str] = []
        for entry in resp.json().get("files", []):
            full = f"{path}{entry}"
            if entry.endswith("/"):
                files.extend(self._list_files(full))
            elif entry.endswith(".md"):
                files.append(full)
        return files

    def get_all_notes(self, max_notes: int = 50) -> list[dict]:
        """Read vault notes as [{path, content}]. Bounded to keep the LLM
        extraction context sane on big vaults."""
        notes: list[dict] = []
        for path in self._list_files()[:max_notes]:
            try:
                resp = httpx.get(
                    f"{self._base_url}/vault/{path}",
                    headers={**self._headers, "Accept": "text/markdown"},
                    verify=self._verify,
                    timeout=10.0,
                )
                resp.raise_for_status()
                notes.append({"path": path, "content": resp.text})
            except Exception as exc:  # noqa: BLE001
                logger.warning("could not read note %s: %s", path, exc)
        return notes

    def extract_monitored_items(self) -> list[MonitoredItem]:
        """LLM reads vault, infers what the user owns/subscribes to.
        Returns SUGGESTIONS ONLY — the user approves each in the dashboard
        before monitoring starts; nothing is auto-registered."""
        from core.config import get_settings as _gs  # noqa: F401  (kept simple)
        from services.llm import LLMService

        notes = self.get_all_notes()
        if not notes:
            return []
        corpus = "\n\n".join(
            f"--- {n['path']} ---\n{n['content'][:2000]}" for n in notes
        )[:40000]

        raw = LLMService().complete_json(
            _EXTRACT_SYSTEM, f"Vault notes:\n\n{corpus}", max_tokens=1500
        )
        items: list[MonitoredItem] = []
        for entry in raw.get("items", []):
            name = str(entry.get("name", "")).strip()
            if not name:
                continue
            items.append(
                MonitoredItem(
                    user_id="",  # filled by the repository at insert time
                    name=name,
                    category=str(entry.get("category", "")),
                    subreddits=[
                        str(s).removeprefix("r/")
                        for s in entry.get("subreddits", [])
                    ][:4],
                )
            )
        return items

    def write_alert_note(self, alert_summary: str) -> None:
        """Append an alert line to 'laeria alerts.md' in the vault root."""
        resp = httpx.post(
            f"{self._base_url}/vault/laeria alerts.md",
            headers={**self._headers, "Content-Type": "text/markdown"},
            content=f"\n- {alert_summary}\n",
            verify=self._verify,
            timeout=10.0,
        )
        resp.raise_for_status()

    def write_action_log(self, action_summary: str) -> None:
        """Append an entry to 'laeria actions.md' in the vault root."""
        from datetime import datetime, timezone

        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        resp = httpx.post(
            f"{self._base_url}/vault/laeria actions.md",
            headers={**self._headers, "Content-Type": "text/markdown"},
            content=f"\n- {stamp} — {action_summary}\n",
            verify=self._verify,
            timeout=10.0,
        )
        resp.raise_for_status()


_EXTRACT_SYSTEM = """You read a user's personal notes and identify products, \
services, and subscriptions they OWN or PAY FOR that are worth monitoring on \
Reddit for problems (quality decline, outages, recalls, price hikes, \
shutdowns). Respond with JSON only:

{"items": [{"name": "...", "category": "...", "subreddits": ["sub1", "sub2"]}]}

Rules:
- Only things the notes indicate the user actually owns/subscribes to — not
  things they merely mention, want, or research.
- "name": the concrete product/service name as searchable on Reddit.
- "subreddits": 1-4 real, active subreddits where problems with this item
  would surface (the product's own sub, its category sub).
- Empty list when nothing qualifies. Never invent items."""
