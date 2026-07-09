"""LLM access via OpenRouter (OpenAI-compatible API).

Connection setup is real; higher-level synthesis methods are implemented
in Phase 1 (decision synthesis) and Phase 2 (outcome classification).
"""

from __future__ import annotations

from openai import OpenAI

from core.config import get_settings
from core.logging import get_logger

logger = get_logger(__name__)


class LLMService:
    def __init__(self) -> None:
        settings = get_settings()
        self._client = OpenAI(
            base_url=settings.openrouter_base_url,
            api_key=settings.openrouter_api_key,
        )
        self._model = settings.openrouter_model

    def healthcheck(self) -> bool:
        """Minimal completion to confirm the key + model work."""
        try:
            resp = self._client.chat.completions.create(
                model=self._model,
                messages=[{"role": "user", "content": "reply with: ok"}],
                max_tokens=5,
            )
            return bool(resp.choices)
        except Exception as exc:  # noqa: BLE001
            logger.error("LLM healthcheck failed: %s", exc)
            return False

    def complete(
        self, system: str, user: str, max_tokens: int = 2000, temperature: float = 0.3
    ) -> str:
        """Generic completion. Real, usable now — agents build on top of this."""
        resp = self._client.chat.completions.create(
            model=self._model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            max_tokens=max_tokens,
            temperature=temperature,
        )
        return resp.choices[0].message.content or ""

    def complete_json(self, system: str, user: str, max_tokens: int = 2000) -> str:
        """Completion constrained to JSON output. Parsing is caller's job."""
        raise NotImplementedError(
            "Phase 1: JSON-mode completion with schema enforcement"
        )
