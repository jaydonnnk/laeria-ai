"""Test-suite guarantees that hold before any test runs.

THE TEST SUITE NEVER TALKS TO AWS.

`Settings` reads `backend/.env`, so a developer with working Bedrock
credentials on their laptop would otherwise run the entire suite against the
real guardrail: every agent test would make live `ApplyGuardrail` calls, and on
a machine without credentials the same tests would fail closed and go red for a
reason that has nothing to do with what they test.

Forcing the flag off here makes "guardrails disabled is a clean no-op" a
property the whole suite proves continuously, rather than a claim in a
docstring. Tests that need guardrail behaviour inject a fake client and fake
settings explicitly — see tests/test_bedrock_guardrails.py — which is also the
only way to get deterministic verdicts.

Set at import time, before any test module is loaded: an environment variable
outranks the `.env` file in pydantic-settings, and the caches are cleared so
nothing that read the setting earlier can survive.
"""

from __future__ import annotations

import os

os.environ["BEDROCK_GUARDRAILS_ENABLED"] = "false"
# Not a credential, and never used: boto3 is not reached from any test. It is
# here so a mistake that DOES reach boto3 fails as an obvious error rather than
# silently picking up the developer's own instance credentials.
os.environ.setdefault("AWS_EC2_METADATA_DISABLED", "true")

# THE SUITE OWNS ITS REDDIT MODE.
#
# `Settings` reads backend/.env, so without this the result of a test run
# depends on whichever mode the developer last set for a demo. That is not
# hypothetical: setting REDDIT_SOURCE=fixture turned 15 guardrail tests red,
# because in fixture mode the research agent raises NoRecordedPlan for a query
# the corpus never captured and returns an empty brief BEFORE any guardrail
# code runs — so the assertions had nothing to observe and the failure looked
# like a security regression.
#
# Pinned to the code default rather than to `fixture`: tests inject fakes for
# RedditService, so nothing here reaches the network, and this keeps the suite
# measuring the path production actually takes. A test that wants replay says
# so itself with monkeypatch.setenv + get_settings.cache_clear().
os.environ["REDDIT_SOURCE"] = "live_then_fixture"

# Discovery is an outbound HTTP call to a search provider. Absent a key the
# provider is a no-op, but pinning it empty means a developer's real key can
# never be spent by a test run.
os.environ["DISCOVERY_PROVIDER"] = "none"
os.environ["DISCOVERY_API_KEY"] = ""

from core.config import get_settings  # noqa: E402
from services.bedrock_guardrails import get_guardrails  # noqa: E402

get_settings.cache_clear()
get_guardrails.cache_clear()

assert get_settings().bedrock_guardrails_enabled is False, (
    "the test suite must run with Bedrock guardrails disabled"
)
assert get_settings().reddit_source == "live_then_fixture", (
    "the test suite must control its own Reddit mode, not inherit it from .env"
)
