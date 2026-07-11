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

    # Reasoning models (deepseek-v4, o-series, etc.) spend completion tokens on
    # hidden reasoning BEFORE any content. A small max_tokens gets entirely
    # consumed by reasoning and content comes back empty. So: generous floor,
    # and ask OpenRouter to keep reasoning effort low for these workhorse calls.
    _MIN_COMPLETION_TOKENS = 4000
    _EXTRA_BODY = {"reasoning": {"effort": "low"}}

    def complete(
        self, system: str, user: str, max_tokens: int = 4000, temperature: float = 0.3
    ) -> str:
        """Generic completion. Real, usable now — agents build on top of this."""
        resp = self._client.chat.completions.create(
            model=self._model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            max_tokens=max(max_tokens, self._MIN_COMPLETION_TOKENS),
            temperature=temperature,
            extra_body=self._EXTRA_BODY,
        )
        return resp.choices[0].message.content or ""

    def complete_json(
        self, system: str, user: str, max_tokens: int = 4000, temperature: float = 0.2
    ) -> dict:
        """Completion that must return a JSON object. Tries the provider's
        JSON mode first; falls back to plain completion + fence stripping.
        Raises ValueError if the model output isn't parseable JSON — callers
        should treat that as a failed run, not silently continue."""
        raw = ""
        try:
            resp = self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                max_tokens=max(max_tokens, self._MIN_COMPLETION_TOKENS),
                temperature=temperature,
                response_format={"type": "json_object"},
                extra_body=self._EXTRA_BODY,
            )
            raw = resp.choices[0].message.content or ""
        except Exception as exc:  # noqa: BLE001
            logger.warning("json mode raised (%s); falling back to plain", exc)

        if not raw.strip():
            # Some models accept response_format but return empty content
            # (reasoning consumed the budget, or json mode unsupported).
            logger.warning("json mode returned empty; falling back to plain")
            raw = self.complete(
                system + "\n\nRespond with the JSON object only — no prose, no fences.",
                user,
                max_tokens=max_tokens,
                temperature=temperature,
            )

        return _parse_json_lenient(raw)


def _parse_json_lenient(raw: str) -> dict:
    """Parse model output into a dict, tolerating markdown fences and
    leading/trailing prose. Raises ValueError when nothing parses."""
    import json
    import re

    text = raw.strip()
    # Strip ```json ... ``` fences if present.
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Last resort: grab the outermost {...} block.
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                pass
    raise ValueError(f"model did not return valid JSON: {raw[:200]!r}")
