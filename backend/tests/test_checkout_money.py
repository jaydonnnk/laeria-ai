"""Checkout total parsing — the money gate's only input.

`_money_after` is the function the ceiling gate depends on: it reads the order
total off the rendered checkout page, and the executor refuses to touch a card
field when it returns None. So both directions matter — reading the wrong
number spends more than was approved, and failing to read a legitimate one
means the checkout can never run at all.

The second failure is the one that shipped: the pattern hardcoded `$`, so an
SGD-priced store returned None on every read.
"""
from __future__ import annotations

from services.checkout import _money_after

TOTAL = r"\bTotal\b"


def test_reads_a_plain_dollar_total():
    assert _money_after(TOTAL, "Total $24.95") == 24.95


def test_reads_an_sgd_total_written_with_the_singapore_symbol():
    """The case that made the gate unusable on a non-USD store."""
    assert _money_after(TOTAL, "Total S$24.95") == 24.95


def test_reads_a_total_written_with_a_currency_code():
    assert _money_after(TOTAL, "Total SGD 1,234.56") == 1234.56


def test_reads_a_total_written_with_both_code_and_symbol():
    assert _money_after(TOTAL, "Total SGD $49.90") == 49.90


def test_subtotal_does_not_satisfy_the_total_label():
    """Word-boundary check: the gate must compare the FINAL total, and
    "Subtotal" appears first on every checkout page."""
    page = "Subtotal $10.00\nShipping $4.00\nTotal $14.00"
    assert _money_after(TOTAL, page) == 14.00


def test_an_amount_with_no_currency_marker_is_not_a_price():
    """Fails closed: the caller raises CheckoutTotalUnreadable rather than
    guessing, because a bare number next to "Total" is as likely to be an
    item count as an amount."""
    assert _money_after(TOTAL, "Total 24.95") is None


def test_an_item_count_is_not_read_as_money():
    assert _money_after(TOTAL, "Total 2 items") is None


def test_a_missing_label_reads_as_unreadable():
    assert _money_after(TOTAL, "Order summary\nNothing here") is None


def test_an_amount_far_past_the_label_is_not_attributed_to_it():
    """A price 200 characters below the word "Total" belongs to some other
    row of the page."""
    page = "Total" + (" " * 200) + "$99.99"
    assert _money_after(TOTAL, page) is None


def test_whole_currency_amounts_still_need_their_cents():
    """Storefronts write totals with cents; requiring them is what makes the
    "2 items" case above safe."""
    assert _money_after(TOTAL, "Total $25") is None
    assert _money_after(TOTAL, "Total $25.00") == 25.00
