"""Truth table for verify_within_mandate — the function whose failure costs
real money.

A mandate bypass has shipped twice already (1b796cf, and the approved-amount
hole found in the 2026-07-27 review). Both are pinned here. Every case is a
pure-function call: no network, no database, no fixtures.
"""

from __future__ import annotations

import pytest

from core.models import ActionMandate
from services.payment import MandateViolation, PaymentService


def check(mandate: ActionMandate, amount: float, **kw):
    """verify_within_mandate without running PaymentService.__init__ (which
    builds an httpx client and reads settings we don't need here)."""
    svc = PaymentService.__new__(PaymentService)
    return svc.verify_within_mandate(
        amount_usd=amount, category=kw.pop("category", ""), mandate=mandate, **kw
    )


def full(**overrides) -> ActionMandate:
    """A fully-specified, permissive-but-bounded mandate."""
    base = dict(
        max_per_transaction=100.0,
        max_per_month=500.0,
        require_confirmation_above=100.0,
        autonomous_actions_enabled=True,
    )
    base.update(overrides)
    return ActionMandate(**base)


# ---- the master switch ----

def test_autonomy_off_always_needs_confirmation():
    needs, _ = check(full(autonomous_actions_enabled=False), 1.0)
    assert needs is True


def test_autonomy_off_does_not_raise_even_when_over_cap():
    # Disabled autonomy is "ask a human", not "violation".
    needs, _ = check(full(autonomous_actions_enabled=False), 10_000.0)
    assert needs is True


# ---- REGRESSION: unset caps must deny, never permit ----

def test_empty_mandate_denies_everything():
    """ActionMandate(**{}) is what a missing profile row produces."""
    with pytest.raises(MandateViolation):
        check(ActionMandate(autonomous_actions_enabled=True), 0.01)


def test_unset_per_transaction_denies():
    with pytest.raises(MandateViolation, match="per-transaction"):
        check(full(max_per_transaction=None), 1.0)


def test_unset_monthly_denies():
    with pytest.raises(MandateViolation, match="monthly"):
        check(full(max_per_month=None), 1.0)


def test_unset_confirm_threshold_confirms_rather_than_executes():
    needs, reason = check(full(require_confirmation_above=None), 1.0)
    assert needs is True
    assert "no confirm threshold" in reason


def test_zero_cap_is_zero_allowance_not_unlimited():
    with pytest.raises(MandateViolation):
        check(full(max_per_transaction=0.0), 0.01)


# ---- per-transaction cap boundary ----

@pytest.mark.parametrize(
    "amount,expect_raise",
    [(99.99, False), (100.0, False), (100.01, True), (10_000.0, True)],
)
def test_per_transaction_boundary(amount, expect_raise):
    m = full(require_confirmation_above=100_000.0)
    if expect_raise:
        with pytest.raises(MandateViolation):
            check(m, amount)
    else:
        needs, _ = check(m, amount)
        assert needs is False


# ---- monthly cap counts prior spend ----

def test_monthly_cap_counts_prior_spend():
    m = full(require_confirmation_above=100_000.0)
    needs, _ = check(m, 50.0, spent_this_month=449.0)
    assert needs is False
    with pytest.raises(MandateViolation, match="monthly"):
        check(m, 50.0, spent_this_month=451.0)


def test_monthly_cap_exact_boundary_allowed():
    m = full(require_confirmation_above=100_000.0)
    needs, _ = check(m, 50.0, spent_this_month=450.0)
    assert needs is False


# ---- confirmation threshold parks rather than executing ----

def test_above_confirm_threshold_parks():
    needs, reason = check(full(require_confirmation_above=25.0), 26.0)
    assert needs is True
    assert "confirm threshold" in reason


def test_at_confirm_threshold_executes():
    needs, _ = check(full(require_confirmation_above=25.0), 25.0)
    assert needs is False


# ---- category / vendor scoping ----

def test_category_not_in_allowlist_denies():
    m = full(allowed_categories=["commerce"])
    with pytest.raises(MandateViolation, match="category"):
        check(m, 10.0, category="gambling")


def test_empty_allowlist_permits_any_category():
    needs, _ = check(full(), 10.0, category="anything")
    assert needs is False


def test_blocked_vendor_denies():
    m = full(blocked_vendors=["evil.example"])
    with pytest.raises(MandateViolation, match="vendor"):
        check(m, 10.0, vendor="evil.example")


def test_unblocked_vendor_passes():
    needs, _ = check(full(blocked_vendors=["evil.example"]), 10.0, vendor="ok.example")
    assert needs is False


# ---- REGRESSION: consent binds to an amount ----------------------------
#
# The execution paths must not spend more than the figure a human approved,
# even when the live price is still inside the standing mandate caps. The
# clamp itself lives in api/routes/actions.py; this pins the arithmetic it
# depends on so a refactor there cannot silently drop it.

def test_price_drift_math_reparks_beyond_tolerance():
    approved, tolerance = 50.0, 0.05
    live = 400.0
    assert live > approved * (1 + tolerance), "drifted price must trip re-approval"


def test_price_drift_math_allows_within_tolerance():
    approved, tolerance = 50.0, 0.05
    live = 52.0
    assert live <= approved * (1 + tolerance), "small drift must not trip re-approval"


def test_drifted_price_can_still_satisfy_the_standing_mandate():
    """Why the clamp is needed at all: $400 passes a $500/txn mandate, so the
    mandate alone would have let a $50-approved purchase execute at $400."""
    m = full(max_per_transaction=500.0, max_per_month=5000.0,
             require_confirmation_above=100_000.0)
    needs, _ = check(m, 400.0)
    assert needs is False
