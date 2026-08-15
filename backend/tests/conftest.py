"""Test-suite guarantees that hold before any test runs.

THE TEST SUITE NEVER TALKS TO AWS.

`Settings` reads `backend/.env`, so a developer with working Bedrock
credentials on their laptop would otherwise run the entire suite against the
real guardrail: every agent test would make live `ApplyGuardrail` calls, and on
a machine without credentials the same tests would fail closed and go red for
a reason that has nothing to do with what they test.

Forcing the flag off here makes "guardrails disabled is a clean no-op" a
property the whole suite proves continuously, rather than a claim in a
docstring. Tests that need guardrail behaviour inject a fake client or a fake
settings object explicitly — see tests/test_bedrock_guardrails.py — which is
also the only way to get deterministic verdicts.

Set at import time, before any test module is loaded: an environment variable
outranks the `.env` file in pydantic-settings, and the caches are cleared so
nothing that read the setting earlier can survive.
"""

from __future__ import annotations

import os

os.environ["BEDROCK_GUARDRAILS_ENABLED"] = "false"
# Not a real key, and never used: boto3 is not reached from any test. It is
# here so that a mistake which DOES reach boto3 fails as an obvious explicit
# error rather than silently picking up the developer's own credentials.
os.environ.setdefault("AWS_EC2_METADATA_DISABLED", "true")

from core.config import get_settings  # noqa: E402
from services.bedrock_guardrails import get_guardrails  # noqa: E402

get_settings.cache_clear()
get_guardrails.cache_clear()

assert get_settings().bedrock_guardrails_enabled is False, (
    "the test suite must run with Bedrock guardrails disabled"
)
