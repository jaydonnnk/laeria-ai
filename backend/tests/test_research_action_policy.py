"""/research/act reads the verdict it computed, not the one it was handed.

The endpoint that spends used to take `confidence` and `consensus_pick` from
the request body while describing itself as a server-side integrity gate. A
caller could therefore label its own research HIGH, or point the purchase at a
product the research never recommended. These tests hold both doors shut, and
prove a pre-upgrade cache entry cannot pose as a calibrated brief.

No network, no wallet, no chain, no card issuer: `propose_action` is stubbed at
the boundary, so nothing here can spend anything. The mandate pipeline behind
it is deliberately untouched by this phase and is covered by test_mandate.py.
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from agents.research_agent import effective_query
from core.models import ConfidenceLevel, EvidenceState, ResearchBrief, SignalQuality
from services import research_cache

HIGH = ConfidenceLevel.HIGH
MODERATE = ConfidenceLevel.MODERATE
LOW = ConfidenceLevel.LOW


@pytest.fixture(autouse=True)
def isolated_cache(tmp_path, monkeypatch):
    """Point the research cache at a temp dir — never the developer's own."""
    monkeypatch.setattr(research_cache, "CACHE_DIR", tmp_path / "research")
    return tmp_path


@pytest.fixture
def proposals(monkeypatch):
    """Capture what would have been proposed, without proposing anything."""
    seen: list[dict] = []

    def _fake_propose(req):
        seen.append(req.model_dump())
        return {"action": {"id": "act-1", "status": "approved"}, "outcome": "stubbed"}

    import api.routes.actions as actions

    monkeypatch.setattr(actions, "propose_action", _fake_propose)
    return seen


def _brief(confidence: ConfidenceLevel, pick: str = "The Keychron K2 HE.") -> dict:
    return ResearchBrief(
        consensus_pick=pick,
        confidence=confidence,
        semantic_confidence=confidence,
        structural_ceiling=confidence,
        signal_quality=SignalQuality(evidence_state=EvidenceState.OK),
    ).model_dump(mode="json")


def _store(query: str, brief: dict, kind: str | None = None) -> None:
    research_cache.put(query, brief, kind=kind or research_cache.DECISION_CACHE_KIND)


def _act(query: str, context: str = "", **extra):
    """Call the endpoint the way a client would, extras included."""
    from api.routes.research import ActOnBriefRequest, act_on_brief

    return act_on_brief(ActOnBriefRequest(query=query, context=context, **extra))


# ---- the forged-client attacks -------------------------------------------


def test_case21_a_client_claiming_high_cannot_act_on_a_low_brief(proposals):
    """The headline attack: the caller asserts HIGH, the server knows better."""
    _store("is the Steam Deck OLED worth it", _brief(LOW))

    with pytest.raises(HTTPException) as exc:
        _act("is the Steam Deck OLED worth it", confidence="high")

    assert exc.value.status_code == 409
    assert "low-confidence" in exc.value.detail
    assert proposals == [], "a refused action must not reach the spending path"


def test_case25_a_client_supplied_pick_can_never_reach_the_proposal(proposals):
    _store("best mechanical keyboard", _brief(HIGH, pick="The Keychron K2 HE."))

    _act("best mechanical keyboard", consensus_pick="A Rolex Submariner")

    assert len(proposals) == 1
    description = proposals[0]["description"]
    assert "Keychron K2 HE" in description, "the authoritative pick was not used"
    assert "Rolex" not in description, "the client redirected the purchase"


def test_a_forged_pick_cannot_smuggle_itself_in_alongside_a_forged_level(proposals):
    """Both doors at once."""
    _store("best headphones", _brief(LOW, pick="The Sony XM5."))

    with pytest.raises(HTTPException) as exc:
        _act("best headphones", confidence="high", consensus_pick="A gold bar")

    assert exc.value.status_code == 409
    assert proposals == []


# ---- fail-closed ---------------------------------------------------------


def test_case24_a_missing_brief_refuses_rather_than_trusting_the_caller(proposals):
    with pytest.raises(HTTPException) as exc:
        _act("a question never researched", confidence="high")

    assert exc.value.status_code == 409
    assert "run the research" in exc.value.detail
    assert proposals == []


def test_an_unreadable_cache_entry_is_not_an_authority(proposals):
    _store("broken", {"confidence": "not-a-level", "consensus_pick": 12345})

    with pytest.raises(HTTPException) as exc:
        _act("broken")

    assert exc.value.status_code == 409
    assert proposals == []


def test_a_brief_with_no_pick_is_refused(proposals):
    brief = _brief(HIGH, pick="")
    brief["confidence"] = "high"  # force the incoherent shape directly
    _store("empty pick", brief)

    with pytest.raises(HTTPException) as exc:
        _act("empty pick")

    assert exc.value.status_code == 409
    assert proposals == []


# ---- the eligible levels -------------------------------------------------


def test_case22_high_confidence_research_reaches_the_proposal(proposals):
    _store("best keyboard", _brief(HIGH))
    result = _act("best keyboard")
    assert len(proposals) == 1
    assert result["outcome"] == "stubbed"


def test_case23_moderate_remains_eligible_in_phase_a(proposals):
    _store("best keyboard", _brief(MODERATE))
    _act("best keyboard")
    assert len(proposals) == 1


def test_case28_low_confidence_research_never_reaches_propose_action(proposals):
    _store("best keyboard", _brief(LOW))
    with pytest.raises(HTTPException):
        _act("best keyboard")
    assert proposals == []


def test_case27_an_eligible_action_still_goes_through_the_unchanged_mandate(proposals):
    """Phase A gates whether to ASK; the mandate still decides the money."""
    _store("best keyboard", _brief(HIGH))
    _act("best keyboard")
    proposed = proposals[0]
    assert proposed["type"] == "purchase"
    assert proposed["category"] == "research"
    # No amount, no rail override, no bypass: the mandate pipeline is entered
    # exactly as any other proposal enters it.
    assert "amount_usd" not in proposed


# ---- context-bearing research -------------------------------------------


def test_case26_a_context_bearing_decision_finds_its_own_brief(proposals):
    """/decision researches "query (context)"; /act must look up the same key."""
    composed = effective_query("best keyboard", "under $100, Singapore")
    assert composed == "best keyboard (under $100, Singapore)"
    _store(composed, _brief(HIGH))

    _act("best keyboard", context="under $100, Singapore")

    assert len(proposals) == 1, "the context-bearing brief was not found"


def test_context_bearing_research_is_not_satisfied_by_the_bare_query(proposals):
    """A different question must not borrow another question's authority."""
    _store("best keyboard", _brief(HIGH))

    with pytest.raises(HTTPException) as exc:
        _act("best keyboard", context="under $100")

    assert exc.value.status_code == 409
    assert proposals == []


def test_the_effective_query_helper_is_whitespace_stable():
    assert effective_query("  best keyboard  ", "  under $100 ") == (
        "best keyboard (under $100)"
    )
    assert effective_query("best keyboard", "") == "best keyboard"
    assert effective_query("best keyboard", "   ") == "best keyboard"


# ---- cache versioning ----------------------------------------------------


def test_case13_a_legacy_decision_entry_cannot_pose_as_a_calibrated_brief(proposals):
    """A pre-upgrade brief carries an LLM-only verdict with no ceiling applied.

    Served from cache it would present itself as structurally calibrated, for a
    whole TTL after the upgrade shipped. The kind is versioned so it simply
    misses.
    """
    legacy = {
        "consensus_pick": "The Keychron K2 HE.",
        "confidence": "high",          # authored by the model alone
        "signal_quality": {"subreddits_checked": ["MechanicalKeyboards"]},
    }
    _store("legacy question", legacy, kind="decision")   # the OLD kind

    # The v2 reader does not see it.
    assert (
        research_cache.get(
            "legacy question", kind=research_cache.DECISION_CACHE_KIND, ttl_seconds=86_400
        )
        is None
    )

    # And the spending path therefore refuses rather than acting on it.
    with pytest.raises(HTTPException) as exc:
        _act("legacy question")
    assert exc.value.status_code == 409
    assert proposals == []


def test_the_legacy_entry_is_preserved_not_deleted():
    """Migration by version bump, not by destroying the user's cache."""
    legacy = {"consensus_pick": "x", "confidence": "high"}
    _store("still there", legacy, kind="decision")

    research_cache.get(
        "still there", kind=research_cache.DECISION_CACHE_KIND, ttl_seconds=86_400
    )

    assert (
        research_cache.get("still there", kind="decision", ttl_seconds=86_400) == legacy
    ), "the old entry was destroyed rather than bypassed"


def test_the_two_kinds_occupy_different_keys():
    assert research_cache._key("q", "", "decision") != research_cache._key(
        "q", "", research_cache.DECISION_CACHE_KIND
    )


def test_a_v2_brief_round_trips_with_its_calibration_intact():
    """The cache must carry the ceiling and reasons, not just the final value."""
    brief = ResearchBrief(
        consensus_pick="The Keychron K2 HE.",
        confidence=MODERATE,
        semantic_confidence=HIGH,
        structural_ceiling=MODERATE,
        confidence_reasons=["Evidence is represented in 2 communities; ..."],
    )
    _store("round trip", brief.model_dump(mode="json"))

    restored = ResearchBrief.model_validate(
        research_cache.get(
            "round trip", kind=research_cache.DECISION_CACHE_KIND, ttl_seconds=86_400
        )
    )
    assert restored.confidence is MODERATE
    assert restored.semantic_confidence is HIGH
    assert restored.structural_ceiling is MODERATE
    assert restored.confidence_reasons == ["Evidence is represented in 2 communities; ..."]


def test_a_disabled_research_cache_refuses_with_advice_that_can_work(
    proposals, monkeypatch
):
    """CASE 7: RESEARCH_CACHE_SECONDS=0 disables this endpoint's authority.

    Telling the user to "run the research again" would be advice that cannot
    succeed — the brief would be written and then be unreadable, forever. The
    refusal has to name the configuration instead.
    """
    from core.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "research_cache_seconds", 0, raising=False)

    with pytest.raises(HTTPException) as exc:
        _act("best keyboard")

    assert exc.value.status_code == 409
    detail = exc.value.detail.lower()
    assert "disabled" in detail
    assert "run the research again" not in detail, (
        "gave advice that cannot work in this configuration"
    )
    assert proposals == []


def test_a_disabled_cache_refuses_even_when_a_brief_was_somehow_written(
    proposals, monkeypatch
):
    """Fail closed on configuration, not on whether a file happens to exist."""
    _store("best keyboard", _brief(HIGH))

    from core.config import get_settings

    monkeypatch.setattr(get_settings(), "research_cache_seconds", 0, raising=False)

    with pytest.raises(HTTPException) as exc:
        _act("best keyboard")
    assert exc.value.status_code == 409
    assert proposals == []


def test_the_default_configuration_still_permits_acting(proposals):
    """The guard above must not affect the normal demo configuration."""
    from core.config import get_settings

    assert get_settings().research_cache_seconds > 0, "default demo config changed"
    _store("best keyboard", _brief(HIGH))
    _act("best keyboard")
    assert len(proposals) == 1


def test_act_still_requires_the_owner():
    """Phase A must not have loosened the auth gate while changing the body."""
    from api.main import app

    for route in app.routes:
        if getattr(route, "path", "") == "/research/act":
            names = {
                getattr(d.dependency, "__name__", "")
                for d in getattr(route, "dependencies", [])
            }
            assert "require_owner" in names
            return
    pytest.fail("/research/act not found")
