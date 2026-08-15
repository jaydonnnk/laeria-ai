"""Amazon Bedrock Guardrails — the independent safety boundary around the agent.

This is NOT an inference provider. OpenRouter still does all of Laeria's
thinking. Bedrock is asked one question only, at every point where text crosses
a trust boundary:

    "Can this content safely enter or leave the agent?"

Three boundaries use it, across all three agents — research, shopping and
monitoring (see those modules for the call sites):

    user instruction ──► [guardrail: INPUT] ──► planner / retrieval / LLM
    Reddit + merchant text ──► [guardrail: INPUT] ──► LLM context
    model output ──► [guardrail: OUTPUT] ──► user / alert / action proposal

External content is checked with source=INPUT deliberately, not OUTPUT: the
PROMPT_ATTACK filter only runs on the INPUT side, and prompt injection hidden
in a Reddit comment or a product title is exactly the threat that boundary
exists to catch.

WHAT THIS MODULE OWNS
    All boto3 knowledge. Agents receive `GuardrailVerdict` objects and never
    see a raw AWS response, so the shape of `assessments` is parsed in one
    place and one place only.

WHAT IT NEVER DOES
    Log the content it inspects. That content may hold credentials, private
    keys, personal data or an attacker's instructions. Logs carry the verdict
    and the policy names AWS reported ("PROMPT_ATTACK", "PII:EMAIL") — never
    the text and never the matched substring.

CREDENTIALS
    boto3's standard provider chain, untouched: environment, shared config, or
    the instance/task IAM role in a deployment. This module never reads or
    holds an access key, which is what lets the same code run locally now and
    under an IAM role later.

FAILURE POLICY
    Disabled  -> clean no-op, every verdict allows, no AWS call, no coupling.
    Enabled and AWS answers -> its verdict is final.
    Enabled and AWS cannot be reached -> UNAVAILABLE, which callers treat as a
    refusal. A safety check that did not run is not a safety check that passed;
    at a boundary protecting money, that has to fail closed.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Iterable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from functools import lru_cache

from core.logging import get_logger

logger = get_logger(__name__)

# AWS's own vocabulary for which side of the model the text is on. Kept as
# constants so a typo is an import error rather than a silently unguarded call.
INPUT = "INPUT"
OUTPUT = "OUTPUT"

# What a verdict can say. Deliberately more than a boolean: "we did not check"
# and "we checked and it is fine" must never be the same value, and "masked"
# has to be distinguishable from "clean" because the caller has to swap in the
# sanitized text.
DISABLED = "DISABLED"      # the integration is switched off
NONE = "NONE"              # checked, nothing found
MASKED = "MASKED"          # checked, PII replaced — use the sanitized text
BLOCKED = "BLOCKED"        # checked, refused
UNAVAILABLE = "UNAVAILABLE"  # the check itself could not run

# The most text Laeria will hand to a single ApplyGuardrail call.
#
# NOT AN AWS LIMIT. AWS's standard-tier input allowance is comfortably larger
# than this; this is a conservative application ceiling, chosen so that the
# assembled research prompt — around 50k characters with a full corpus — fits
# in ONE invocation with room to spare, while staying well inside the smaller
# documented text-unit classes.
#
# THERE IS NO CHUNKING. An earlier version split anything longer into 20k
# pieces and checked each, which quietly destroyed the invariant this module
# exists for: two safe chunks do not make a safe whole, and the composed
# meaning of a prompt is exactly what the final check is supposed to see.
# Splitting is not a way to check a large prompt — it is a way to check
# something else and call it the prompt.
#
# So text over this ceiling is REFUSED rather than divided. Nothing in the
# application legitimately approaches it: a user query is capped at 500
# characters, a rendered thread at roughly 8k, a title batch at roughly 8k, and
# the assembled prompt is bounded by the thread budget.
_MAX_GUARDED_CHARS = 100_000

# Guardrail calls for a corpus are independent, so they overlap. Small on
# purpose — this runs alongside Reddit fetches and LLM calls that have pools of
# their own, and the work here is entirely network wait.
_CHECK_CONCURRENCY = 4


# The two sentences a user can ever see from this module, defined once. They
# are shown verbatim by the API, so they say what happened in plain words and
# carry no AWS wording, no status code and no stack trace.
_UNAVAILABLE_MESSAGE = (
    "Safety verification is temporarily unavailable. Laeria did not continue."
)
# Worded to be true at EVERY call site, which is stricter than it looks. An
# earlier version also promised that no search had happened — true for the
# research and shopping boundaries, but false for a monitored item, whose name
# is guarded at the moment it reaches the model and therefore after
# `scan_recent` has already run. A refusal message that overstates what it
# prevented is a small lie in the one place a user is being told the truth.
_BLOCKED_MESSAGE = (
    "This request was refused by Laeria's safety layer. It was not sent to any "
    "model, and no purchase or payment was proposed."
)
_OVERSIZE_MESSAGE = (
    "Safety verification could not evaluate the complete model context. Laeria "
    "did not continue."
)


class GuardrailBlocked(RuntimeError):
    """Bedrock refused this content, so the flow stops here.

    Carries the policy names AWS reported (never the content) so a caller can
    log or surface *why* without going back to the raw response.
    """

    def __init__(
        self, message: str = _BLOCKED_MESSAGE, categories: Sequence[str] = ()
    ) -> None:
        super().__init__(message)
        self.categories = tuple(categories)


class GuardrailUnavailable(RuntimeError):
    """The safety check could not run, so the flow stops here.

    A separate type from `GuardrailBlocked` because the two mean opposite
    things to the person waiting: one is "we will not do this", the other is
    "we could not check, try again". Conflating them would tell a user their
    perfectly ordinary question was refused.

    The message is a default rather than an argument. Every raise site means
    exactly the same thing, and eight hand-copied versions of one user-facing
    sentence is eight chances for them to drift apart.
    """

    def __init__(self, message: str | None = None) -> None:
        super().__init__(message or _UNAVAILABLE_MESSAGE)


@dataclass(frozen=True)
class GuardrailVerdict:
    """One guardrail decision about one piece of text.

    `text` is what downstream code must use. It is the original when nothing
    was found and the SANITIZED version when AWS masked something — callers
    that reach past this field to the original would undo the masking, which is
    the whole reason the field exists.
    """

    allowed: bool
    text: str
    action: str
    categories: tuple[str, ...] = ()
    latency_ms: int = 0

    @property
    def masked(self) -> bool:
        return self.action == MASKED

    @property
    def blocked(self) -> bool:
        return self.action == BLOCKED

    @property
    def unavailable(self) -> bool:
        return self.action == UNAVAILABLE

    @property
    def checked(self) -> bool:
        """Did a real guardrail call decide this?

        False for both DISABLED and UNAVAILABLE — neither is evidence that the
        content is safe, and code that needs to know whether a check actually
        ran must not have to infer it from `allowed`.
        """
        return self.action in (NONE, MASKED, BLOCKED)

    @property
    def reason(self) -> str:
        """A short, safe explanation. Contains policy names, never content."""
        return ", ".join(self.categories) if self.categories else self.action


@dataclass(frozen=True)
class ScreenedBatch:
    """The result of guarding a list of short lines that go into one prompt."""

    # Indices of the input lines that survived, so a caller can line the
    # verdict back up with whatever objects it rendered from.
    kept: tuple[int, ...]
    # The block to actually send — sanitized, and containing only the lines
    # that survived.
    text: str
    dropped: int = 0


class BedrockGuardrails:
    """Applies one configured Bedrock guardrail to text crossing a boundary.

    The boto3 client is created lazily and can be injected, so every test in
    this repository runs without AWS credentials, without network, and without
    boto3 having to be importable at test time.
    """

    def __init__(self, client=None, settings=None) -> None:  # noqa: ANN001
        from core.config import get_settings

        self._settings = settings or get_settings()
        self._client = client
        self._client_lock = threading.Lock()
        # `enabled` is the OPERATOR'S INTENT and nothing else. It is never
        # downgraded by a configuration problem.
        #
        # Turning "enabled but unusable" into "disabled" would be the worst
        # possible reading of a misconfiguration: an operator who set the flag
        # believes the boundary is up, and every protected call would sail
        # straight through it. Broken configuration is an outage, not an
        # opt-out, so it is recorded separately and every verdict comes back
        # UNAVAILABLE — which every caller already treats as a refusal.
        self._enabled = bool(self._settings.bedrock_guardrails_enabled)
        self._config_error = self._configuration_problem()
        if self._config_error:
            logger.error(
                "bedrock guardrails are ENABLED but unusable (%s) — every "
                "protected request will be refused until this is fixed",
                self._config_error,
            )

    def _configuration_problem(self) -> str | None:
        """Why this guardrail cannot be trusted to run, or None if it can.

        Checked once, at construction. Everything here would otherwise surface
        as a per-request AWS error, or — worse for the version rule — as a
        boundary that runs happily against a definition that can change under
        it without a deploy.
        """
        if not self._enabled:
            return None
        if not (self._settings.bedrock_guardrail_id or "").strip():
            return "BEDROCK_GUARDRAIL_ID is empty"
        version = (self._settings.bedrock_guardrail_version or "").strip()
        if not version:
            return "BEDROCK_GUARDRAIL_VERSION is empty"
        if version.upper() == "DRAFT":
            # DRAFT is mutable. A security boundary pinned to something that
            # can be edited in a console is not pinned at all, and this project
            # has decided that is not acceptable — so it is refused loudly
            # rather than honoured quietly.
            return "BEDROCK_GUARDRAIL_VERSION is DRAFT, which is mutable"
        return None

    @property
    def enabled(self) -> bool:
        """Did the operator ask for guardrails? Not "are they working"."""
        return self._enabled

    @property
    def config_error(self) -> str | None:
        """Why an enabled guardrail cannot run, for operators. Never a secret."""
        return self._config_error

    # ---- the boundary API ----

    def check(self, text: str, source: str) -> GuardrailVerdict:
        """Ask Bedrock whether this text may cross the boundary.

        `source` is INPUT for anything entering the model (a user instruction,
        a Reddit thread, a product title) and OUTPUT for anything the model
        produced.
        """
        if not self._enabled:
            return GuardrailVerdict(allowed=True, text=text, action=DISABLED)
        if self._config_error:
            # Enabled and unusable. Tested before the empty-text shortcut so
            # that a misconfigured guardrail is uniform: nothing it is asked
            # about ever comes back allowed.
            return GuardrailVerdict(
                allowed=False, text=text, action=UNAVAILABLE, categories=("CONFIG",)
            )
        if not text or not text.strip():
            # Nothing to inspect. Calling AWS with empty content is an error
            # response, not a verdict, and an empty string cannot carry an
            # attack.
            return GuardrailVerdict(allowed=True, text=text, action=NONE)
        if len(text) > _MAX_GUARDED_CHARS:
            # Refused, never split. See `_MAX_GUARDED_CHARS`: dividing text to
            # fit would check pieces and then claim the whole had been checked.
            logger.error(
                "guardrail asked to evaluate %d characters, above the %d "
                "ceiling — refusing rather than splitting it",
                len(text), _MAX_GUARDED_CHARS,
            )
            return GuardrailVerdict(
                allowed=False, text=text, action=UNAVAILABLE, categories=("OVERSIZE",)
            )

        # ONE invocation for the whole string. Whatever this returns is a
        # verdict about the text as a unit, which is the only kind of verdict
        # that can back a claim about a composed prompt.
        started = time.monotonic()
        verdict = self._check_one(text, source)
        return replace(verdict, latency_ms=int((time.monotonic() - started) * 1000))

    def check_many(self, texts: Sequence[str], source: str) -> list[GuardrailVerdict]:
        """Check several independent pieces of text, overlapped.

        Order is preserved so a caller can zip verdicts back onto the items
        they came from. Each piece gets its own verdict: one malicious Reddit
        thread should cost that thread, not the whole research run.
        """
        if not self._enabled:
            return [
                GuardrailVerdict(allowed=True, text=t, action=DISABLED) for t in texts
            ]
        if not texts:
            return []
        with ThreadPoolExecutor(max_workers=_CHECK_CONCURRENCY) as pool:
            return list(pool.map(lambda t: self.check(t, source), texts))

    def screen_batch(self, lines: Sequence[str], source: str) -> ScreenedBatch:
        """Guard a list of short lines destined for one prompt, adaptively.

        Reddit search results and monitored-item posts arrive as dozens of
        one-line summaries that go into a single classification prompt. One
        guardrail call per line would be dozens of calls on every run, for a
        problem that almost never occurs.

        So the whole block is checked as one string first. Only when THAT comes
        back refused does it re-check line by line, to find which ones to drop.
        A clean batch costs one call; a poisoned batch costs the precision it
        needs.

        THE SURVIVORS ARE THEN CHECKED AGAIN, TOGETHER. Line-by-line results
        cannot be recombined on our own authority: an attack can live in the
        RELATIONSHIP between two lines, where each half is innocent alone.
        Bedrock refusing the batch and then being handed the same text back
        because its pieces passed individually would be this function
        overruling the verdict it just asked for.

        If the survivors are still refused, the batch is dropped whole. There
        is no finer attribution available — no individual line explains the
        refusal — and guessing which one to sacrifice would be inventing an
        answer Bedrock did not give. Exactly one re-check, so this cannot
        recurse.

        Masking is honoured in every path: the returned `text` is what should
        be sent, never the caller's original.

        Raises `GuardrailUnavailable` rather than returning a verdict, because
        the callers of this have one correct response to an outage: stop.
        """
        items = list(lines)
        if not self._enabled or not items:
            return ScreenedBatch(tuple(range(len(items))), "\n".join(items))

        whole = self.check("\n".join(items), source)
        if whole.unavailable:
            raise GuardrailUnavailable()
        if not whole.blocked:
            return ScreenedBatch(tuple(range(len(items))), whole.text)

        verdicts = self.check_many(items, source)
        if any(v.unavailable for v in verdicts):
            raise GuardrailUnavailable()
        kept: list[int] = []
        texts: list[str] = []
        for i, verdict in enumerate(verdicts):
            if verdict.blocked:
                continue
            kept.append(i)
            texts.append(verdict.text)

        if not kept:
            return ScreenedBatch((), "", len(items))

        survivors = "\n".join(texts)
        recheck = self.check(survivors, source)
        if recheck.unavailable:
            raise GuardrailUnavailable()
        if recheck.blocked:
            logger.warning(
                "guardrail refused %d survivors of a %d-line batch even though "
                "each line passed alone — dropping the batch (%s)",
                len(kept), len(items), recheck.reason,
            )
            return ScreenedBatch((), "", len(items))

        return ScreenedBatch(tuple(kept), recheck.text, len(items) - len(kept))

    def screen_prompt(
        self, prompt: str, label: str, source: str = INPUT
    ) -> tuple[str, bool]:
        """Verify the EXACT string a model is about to be given.

        THE LAST BOUNDARY, and the only one that sees what the model sees.

        Every other check in this module looks at a piece: one question, one
        thread, one product line. Pieces being individually safe does not make
        their combination safe — meaning can live in the relationship between
        two innocent fragments, and a prompt is precisely where fragments get
        combined. `screen_batch` already re-checks its survivors together for
        that reason; this is the same rule applied to the finished prompt.

        Returns `(text_to_send, ok)`. The caller must send the returned text
        and nothing else: checking one string and then assembling a different
        one would make this check decorative.

        `ok=False` means refused. The caller decides how to fail closed in its
        own idiom — an empty brief, a quiet monitoring run, no pick — because
        those are the shapes their callers already understand. An outage raises
        instead, for the same reason it does everywhere else.
        """
        verdict = self.check(prompt, source)
        if verdict.unavailable:
            # A prompt too large to evaluate as one unit is a distinct failure
            # from an outage, and the person waiting deserves to be told which
            # — without being shown a quota number or a boto3 message.
            raise GuardrailUnavailable(
                _OVERSIZE_MESSAGE if "OVERSIZE" in verdict.categories else None
            )
        if verdict.blocked:
            logger.warning(
                "guardrail refused the assembled %s prompt: %s", label, verdict.reason
            )
            return prompt, False
        if verdict.masked:
            logger.warning(
                "guardrail masked the assembled %s prompt: %s", label, verdict.reason
            )
        return verdict.text, True

    def ensure_allowed(self, text: str, source: str, label: str) -> str:
        """Check text at a boundary that must not be crossed unverified.

        Returns the text to use downstream — sanitized when AWS masked
        something. Raises rather than returning a verdict, because the callers
        of this helper have exactly one correct response to a refusal: stop.

        `label` names the boundary in logs ("research query", "shopping
        instruction"). It is a constant in the code, never user content.
        """
        verdict = self.check(text, source)
        # Logged BEFORE the raise, and for every outcome that is not "clean".
        # A refusal is the single most important thing this integration does;
        # if the only record of it were the exception, an operator would learn
        # nothing about which policy fired or how often.
        _log(label, source, verdict)
        if verdict.blocked:
            raise GuardrailBlocked(categories=verdict.categories)
        if verdict.unavailable:
            raise GuardrailUnavailable()
        return verdict.text

    def sanitize_model_output(
        self, payload: dict, fields: Iterable[str], label: str
    ) -> tuple[dict, int]:
        """Guard every model-authored string in `payload` before it is shown.

        Returns a copy with blocked strings removed and masked strings
        replaced, plus how many were removed. A list loses only the offending
        item; a blocked top-level string becomes empty, so the caller's own
        "nothing to show" handling takes over without this module needing to
        know anything about research, shopping or monitoring.

        Raises `GuardrailUnavailable` if the check cannot run — model output
        that nobody verified must not reach a user at a protected boundary.
        """
        names = tuple(fields)
        if not self._enabled:
            return dict(payload), 0

        # Every string is gathered first and checked in ONE overlapped batch;
        # the results are then written back by walking the same fields in the
        # same order. Checking field by field would serialise a dozen network
        # calls for no benefit.
        texts = [text for _, text in _model_strings(payload, names)]
        if not texts:
            return dict(payload), 0

        verdicts = self.check_many(texts, OUTPUT)
        if any(v.unavailable for v in verdicts):
            raise GuardrailUnavailable()

        clean = dict(payload)
        removed = 0
        cursor = 0
        for field in names:
            value = payload.get(field)
            if isinstance(value, str):
                verdict = verdicts[cursor]
                cursor += 1
                if verdict.blocked:
                    removed += 1
                    clean[field] = ""
                else:
                    clean[field] = verdict.text
            elif isinstance(value, list):
                kept: list = []
                for item in value:
                    # Non-strings pass through untouched and in place: a list
                    # can legitimately hold structured entries, and dropping
                    # them because they are not text would be data loss with
                    # nothing to do with safety.
                    if not isinstance(item, str):
                        kept.append(item)
                        continue
                    verdict = verdicts[cursor]
                    cursor += 1
                    if verdict.blocked:
                        removed += 1
                        continue
                    kept.append(verdict.text)
                clean[field] = kept

        if removed:
            logger.warning(
                "bedrock guardrail removed %d model-authored item(s) at %s",
                removed, label,
            )
        return clean, removed

    # ---- AWS ----

    def _bedrock(self):  # noqa: ANN202
        """The boto3 client, created once, on first use.

        Lazy because importing and constructing it costs time and, more
        importantly, because a disabled integration must not require boto3 to
        be installed or credentials to exist.
        """
        if self._client is None:
            with self._client_lock:
                if self._client is None:
                    import boto3

                    self._client = boto3.client(
                        "bedrock-runtime", region_name=self._settings.aws_region
                    )
        return self._client

    def _check_one(self, text: str, source: str) -> GuardrailVerdict:
        try:
            resp = self._bedrock().apply_guardrail(
                guardrailIdentifier=self._settings.bedrock_guardrail_id,
                guardrailVersion=self._settings.bedrock_guardrail_version,
                source=source,
                content=[{"text": {"text": text}}],
                # FULL returns the assessments for everything considered, not
                # only what intervened. That is what makes an intervention
                # explainable in the logs without re-sending the content.
                outputScope="FULL",
            )
        except Exception as exc:  # noqa: BLE001
            # Every failure lands here on purpose: throttling, credentials,
            # region, network. They differ operationally and not at all in what
            # the caller must do, which is refuse to continue. The exception is
            # logged by type and message — never the text it was inspecting.
            logger.error(
                "bedrock guardrail call failed (source=%s): %s: %s",
                source, type(exc).__name__, exc,
            )
            return GuardrailVerdict(
                allowed=False, text=text, action=UNAVAILABLE, categories=()
            )
        return _verdict_from(resp, text)


def _model_strings(payload: dict, fields: Sequence[str]):
    """(field, text) for every model-authored string, in field order.

    The single definition of "which strings does this payload contain", used
    both to build the batch and to write the results back — two walks that
    must agree exactly, so they read from one place.
    """
    for field in fields:
        value = payload.get(field)
        if isinstance(value, str):
            yield field, value
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, str):
                    yield field, item


def _verdict_from(resp: dict, original: str) -> GuardrailVerdict:
    """Turn one ApplyGuardrail response into a verdict.

    The decision is read from the ASSESSMENTS, not from the top-level action
    alone, because AWS reports masking and blocking with the same top-level
    value (`GUARDRAIL_INTERVENED`). Only the per-policy `action` distinguishes
    "we removed an email address, carry on" from "we refuse this".

    Fail closed on anything unrecognised: an intervention whose cause this
    parser cannot identify is treated as a block. A safety layer that guesses
    "probably fine" is not one.
    """
    blocked = False
    masked = False
    categories: list[str] = []

    for assessment in resp.get("assessments") or []:
        for label, action in _findings(assessment):
            if action == "BLOCKED":
                blocked = True
            elif action == "ANONYMIZED":
                masked = True
            else:
                continue
            if label not in categories:
                categories.append(label)

    if blocked:
        return GuardrailVerdict(
            allowed=False, text=original, action=BLOCKED, categories=tuple(categories)
        )

    intervened = resp.get("action") == "GUARDRAIL_INTERVENED"
    if masked:
        # The sanitized text lives in `outputs`. If AWS says it masked
        # something but returns nothing usable, keep the original and refuse:
        # passing unmasked text on while reporting it as masked would be the
        # worst of both.
        sanitized = _first_output_text(resp)
        if not sanitized:
            logger.error("bedrock reported masking but returned no sanitized text")
            return GuardrailVerdict(
                allowed=False, text=original, action=BLOCKED,
                categories=tuple(categories) or ("MASK_WITHOUT_OUTPUT",),
            )
        return GuardrailVerdict(
            allowed=True, text=sanitized, action=MASKED, categories=tuple(categories)
        )

    if intervened:
        # Intervened, but no assessment explains why — an unknown policy shape
        # or a newer response format. Refuse rather than assume.
        logger.error("bedrock intervened with no recognised assessment — refusing")
        return GuardrailVerdict(
            allowed=False, text=original, action=BLOCKED, categories=("UNSPECIFIED",)
        )

    return GuardrailVerdict(allowed=True, text=original, action=NONE)


def _findings(assessment: dict):
    """(label, action) for every policy entry, with no content in the label.

    The `match` fields in these structures hold the offending text itself —
    the email address, the access key, the banned word. They are deliberately
    never read here, because everything this function returns can end up in a
    log line.
    """
    for f in (assessment.get("contentPolicy") or {}).get("filters") or []:
        yield str(f.get("type") or "CONTENT"), f.get("action")
    for t in (assessment.get("topicPolicy") or {}).get("topics") or []:
        # Topic names come from our own guardrail configuration, not from the
        # inspected content.
        yield f"TOPIC:{t.get('name') or 'UNNAMED'}", t.get("action")
    sensitive = assessment.get("sensitiveInformationPolicy") or {}
    for p in sensitive.get("piiEntities") or []:
        yield f"PII:{p.get('type') or 'UNKNOWN'}", p.get("action")
    for r in sensitive.get("regexes") or []:
        yield f"REGEX:{r.get('name') or 'UNNAMED'}", r.get("action")
    word = assessment.get("wordPolicy") or {}
    for w in word.get("customWords") or []:
        yield "WORD", w.get("action")
    for w in word.get("managedWordLists") or []:
        yield f"WORDLIST:{w.get('type') or 'MANAGED'}", w.get("action")


def _first_output_text(resp: dict) -> str:
    for out in resp.get("outputs") or []:
        text = out.get("text")
        if text:
            return str(text)
    return ""


def _log(label: str, source: str, verdict: GuardrailVerdict) -> None:
    """Privacy-safe record of one guardrail decision.

    Everything here is either a constant from the code, an AWS policy name, or
    a number. The inspected text never appears.
    """
    if verdict.action in (DISABLED, NONE):
        return
    logger.warning(
        "bedrock guardrail %s at %s (source=%s): %s [%dms]",
        verdict.action.lower(), label, source, verdict.reason, verdict.latency_ms,
    )


@lru_cache
def get_guardrails() -> BedrockGuardrails:
    """The process-wide guardrail service, mirroring `get_settings`.

    Cached so the boto3 client is built once. Agents accept an injected
    instance instead of calling this, which is what keeps the tests free of
    AWS; this exists for the default construction path.
    """
    return BedrockGuardrails()
