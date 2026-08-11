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
        # An explicit timeout is not optional here. The SDK default is 600
        # seconds, so a single stalled request — observed intermittently
        # against OpenRouter on large synthesis calls — wedges the caller for
        # ten minutes with no way to tell it apart from slow work. A bounded
        # failure can be retried or degraded around; an unbounded one cannot.
        # Retries multiply the timeout rather than bounding it: max_retries=2
        # at a 120s timeout is a six-minute worst case for one call, and that
        # is exactly how a model with a good median and a bad tail produces
        # three-minute reports.
        self._client = OpenAI(
            base_url=settings.openrouter_base_url,
            api_key=settings.openrouter_api_key,
            timeout=float(settings.llm_timeout_seconds),
            max_retries=settings.llm_max_retries,
        )
        self._model = settings.openrouter_model
        # What a single complete_json can cost in the worst case. Callers that
        # impose their own deadline must derive it from this, or they abandon
        # work that was about to land.
        self.worst_case_seconds = settings.llm_timeout_seconds * (
            settings.llm_max_retries + 1
        )

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
        _track(resp)
        return resp.choices[0].message.content or ""

    # Set to False the first time json mode comes back empty/broken for this
    # process's model, so subsequent calls skip the wasted request entirely.
    _json_mode_usable: bool = True

    def complete_json(
        self, system: str, user: str, max_tokens: int = 4000, temperature: float = 0.2
    ) -> dict:
        """Completion that must return a JSON object. Tries the provider's
        JSON mode first (unless it already proved broken for this model);
        falls back to plain completion + fence stripping. Raises ValueError
        if the model output isn't parseable JSON — callers should treat that
        as a failed run, not silently continue."""
        raw = ""
        if LLMService._json_mode_usable:
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
                _track(resp)
                raw = resp.choices[0].message.content or ""
            except Exception as exc:  # noqa: BLE001
                logger.warning("json mode raised (%s); falling back to plain", exc)

            if not raw.strip():
                # Model accepts response_format but returns empty content
                # (seen with deepseek). Don't pay for this call again.
                logger.warning(
                    "json mode unusable for %s; disabled for this process", self._model
                )
                LLMService._json_mode_usable = False

        if not raw.strip():
            raw = self.complete(
                system + "\n\nRespond with the JSON object only — no prose, no fences.",
                user,
                max_tokens=max_tokens,
                temperature=temperature,
            )

        return _parse_json_lenient(raw)

    EMBED_MODEL = "openai/text-embedding-3-small"  # 1536-dim, matches schema

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed texts via OpenRouter's embeddings endpoint. One call for the
        whole batch. Raises on failure — callers treat embedding as
        best-effort and degrade gracefully."""
        resp = self._client.embeddings.create(model=self.EMBED_MODEL, input=texts)
        from core.usage import incr

        incr("embed_calls")
        # Preserve input order via the index field.
        ordered = sorted(resp.data, key=lambda d: d.index)
        return [d.embedding for d in ordered]


def _track(resp) -> None:
    """Record completion usage; never fatal."""
    try:
        from core.usage import incr

        incr("llm_calls")
        if getattr(resp, "usage", None) and resp.usage.total_tokens:
            incr("llm_tokens", resp.usage.total_tokens)
    except Exception:  # noqa: BLE001
        pass


def _balanced_objects(text: str):
    """Every balanced ``{...}`` span in `text`, in order of where it starts.

    A regex cannot do this. The previous `\\{.*\\}` was greedy from the first
    brace to the last, so a model that emitted a stray opening brace before
    its object — observed from deepseek as ``{\\n{"subreddits": ...}`` — made
    the salvage attempt fail on exactly the input it existed to salvage.

    Scanning tracks string literals and escapes, so a brace inside a JSON
    string cannot throw the depth count off.
    """
    for start, ch in enumerate(text):
        if ch != "{":
            continue
        depth = 0
        in_string = False
        escaped = False
        for i in range(start, len(text)):
            c = text[i]
            if in_string:
                if escaped:
                    escaped = False
                elif c == "\\":
                    escaped = True
                elif c == '"':
                    in_string = False
                continue
            if c == '"':
                in_string = True
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    yield text[start : i + 1]
                    break


def _parse_json_lenient(raw: str) -> dict:
    """Parse model output into a dict, tolerating markdown fences, stray
    braces, and leading/trailing prose. Raises ValueError when nothing
    parses — a caller must treat that as a failed run, never as empty data."""
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
        pass

    # Earliest balanced object wins, not the largest: an outer object always
    # starts before the objects nested inside it, so "earliest" picks the
    # whole payload rather than one of its own values.
    for candidate in _balanced_objects(text):
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict) and parsed:
            return parsed

    raise ValueError(f"model did not return valid JSON: {raw[:200]!r}")
