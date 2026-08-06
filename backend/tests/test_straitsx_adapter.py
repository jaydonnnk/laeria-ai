"""StraitsXAdapter against a stand-in server.

The sandbox does not exist until the event, so what is testable now is the
thing most likely to break there: whether the adapter survives a response
whose field names are not the ones guessed. Each test answers with a
different plausible spelling.

If these pass, going live should be env vars rather than a parser rewrite.
"""
from __future__ import annotations

import httpx
import pytest

from core.config import get_settings
from services import cards as C


@pytest.fixture(autouse=True)
def _settings(monkeypatch):
    monkeypatch.setenv("CARD_ISSUER", "straitsx")
    monkeypatch.setenv("STRAITSX_BASE_URL", "https://sandbox.example")
    monkeypatch.setenv("STRAITSX_API_KEY", "test-key")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _adapter(handler) -> C.StraitsXAdapter:
    """A StraitsXAdapter whose transport is a scripted stand-in."""
    a = C.StraitsXAdapter()
    a._http = httpx.Client(
        base_url="https://sandbox.example",
        transport=httpx.MockTransport(handler),
        headers={"Authorization": "Bearer test-key"},
    )
    return a


def test_issue_reads_snake_case():
    def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.path == "/v1/cards"
        body = __import__("json").loads(req.content)
        # The approved amount must reach the issuer as the card's limit.
        assert body["spending_limit"]["amount"] == 42.5
        return httpx.Response(
            201,
            json={"id": "card_1", "last4": "9999", "exp_month": 11, "exp_year": 2030},
        )

    card = _adapter(handler).issue(42.5, merchant_hint="demo store")
    assert (card.issuer_card_id, card.last4, card.spend_limit_usd) == ("card_1", "9999", 42.5)
    assert card.exp_month == 11 and card.exp_year == 2030


def test_issue_reads_camel_case_inside_a_data_envelope():
    """Same card, spelled the other common way. This is the case that would
    otherwise be discovered live at the booth."""

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"data": {"cardId": "card_2", "lastFour": "4321",
                           "expiryMonth": 3, "expiryYear": 2029}},
        )

    card = _adapter(handler).issue(10)
    assert (card.issuer_card_id, card.last4) == ("card_2", "4321")
    assert card.exp_month == 3 and card.exp_year == 2029


def test_issue_without_a_card_id_is_an_error_not_a_silent_blank():
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "pending"})

    with pytest.raises(RuntimeError, match="no card id"):
        _adapter(handler).issue(10)


def test_get_details_accepts_pan_spelling():
    def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.path == "/v1/cards/card_1/secrets"
        return httpx.Response(200, json={"pan": "4111111111111111", "cvv": "737"})

    d = _adapter(handler).get_details("card_1")
    assert d.number == "4111111111111111" and d.cvc == "737"


def test_get_details_without_a_pan_says_what_to_do_instead():
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"masked": "**** 1111"})

    with pytest.raises(RuntimeError, match="CHECKOUT_GATEWAY_PROFILE=bogus"):
        _adapter(handler).get_details("card_1")


def test_cancel_hits_the_terminate_endpoint():
    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["method"], seen["path"] = req.method, req.url.path
        return httpx.Response(200, json={"status": "terminated"})

    _adapter(handler).cancel("card_1")
    assert seen == {"method": "POST", "path": "/v1/cards/card_1/terminate"}


def test_cancel_failure_raises_rather_than_leaving_a_live_card_looking_dead():
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    with pytest.raises(RuntimeError):
        _adapter(handler).cancel("card_1")


def test_transactions_tolerate_an_items_envelope():
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"items": [{"transaction_id": "t1", "amount": -12.5,
                             "status": "captured", "createdAt": "2026-08-06T00:00:00Z"}]},
        )

    rows = _adapter(handler).list_transactions("card_1")
    assert rows == [{"id": "t1", "amount_usd": 12.5, "type": "captured",
                     "created": "2026-08-06T00:00:00Z"}]


def test_transactions_degrade_to_empty_rather_than_breaking_the_receipt():
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="not found")

    assert _adapter(handler).list_transactions("card_1") == []


def test_missing_credentials_name_the_env_vars():
    get_settings.cache_clear()
    import os

    os.environ.pop("STRAITSX_BASE_URL", None)
    get_settings.cache_clear()
    with pytest.raises(RuntimeError, match="STRAITSX_BASE_URL"):
        C.StraitsXAdapter()
