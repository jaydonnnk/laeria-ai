"""The shopping agent picks; the mandate still decides.

A model choosing what to buy is a new way for this pipeline to go wrong, so
the tests are mostly about what happens when the model misbehaves: names a
product that was never on the page, ignores the budget, or fails outright.
None of those may end in a proposal the shopper did not ask for.
"""
from __future__ import annotations

import pytest

from agents.shopping_agent import ShoppingAgent, ShoppingPick


def _product(handle: str, title: str, price: float, available: bool = True) -> dict:
    return {
        "id": handle, "handle": handle, "title": title, "price_usd": price,
        "url": f"https://shop.example/products/{handle}", "image": "",
        "available": available, "variant_id": f"v-{handle}",
        "product_type": "", "vendor": "", "tags": "",
    }


CATALOGUE = [
    _product("ski-wax", "All-Temp Ski Wax", 24.95),
    _product("gold-wax", "Competition Gold Wax", 89.00),
    _product("sold-out-wax", "Race Wax", 19.00, available=False),
]


class _FakeStore:
    """Stands in for StorefrontService. `browser_fails` forces the fallback."""

    def __init__(self, browser_fails: bool = False, handles: list[str] | None = None):
        self._browser_fails = browser_fails
        self._handles = handles if handles is not None else [p["handle"] for p in CATALOGUE]
        self.catalogue_called = False

    def browser_search(self, query: str, limit: int = 12) -> dict:
        if self._browser_fails:
            raise RuntimeError("results page unreadable")
        return {
            "query": query, "url": f"https://shop.example/search?q={query}",
            "handles": self._handles, "screenshot_path": "/tmp/search.png",
        }

    def get_product(self, handle: str) -> dict | None:
        return next((p for p in CATALOGUE if p["handle"] == handle), None)

    def search_products(self, query: str = "", limit: int = 12) -> list[dict]:
        self.catalogue_called = True
        return list(CATALOGUE)


class _FakeLLM:
    """Scripted plan and pick. `raises` makes the pick call blow up."""

    def __init__(self, plan: dict | None = None, pick: dict | None = None, raises=False):
        self._plan = plan or {"query": "ski wax", "max_price": None, "notes": ""}
        self._pick = pick or {"handle": "ski-wax", "reason": "cheapest that fits", "rejected": []}
        self._raises = raises
        self.calls = 0

    def complete_json(self, system: str, user: str, **kw) -> dict:
        self.calls += 1
        if "storefront search" in system:
            return self._plan
        if self._raises:
            raise RuntimeError("model unavailable")
        return self._pick


def _agent(store=None, llm=None) -> ShoppingAgent:
    return ShoppingAgent(storefront=store or _FakeStore(), llm=llm or _FakeLLM())


# ---- the happy path ----

def test_an_instruction_produces_a_located_product():
    pick = _agent().shop("get me some ski wax")
    assert pick.found
    assert (pick.handle, pick.variant_id) == ("ski-wax", "v-ski-wax")
    assert pick.price == 24.95
    assert pick.reason
    assert pick.scanned_via == "browser"


def test_the_pick_carries_the_evidence_it_was_made_from():
    """The search URL and screenshot are the audit trail for "it scanned the
    site" — a claim with no artifact is just a claim."""
    pick = _agent().shop("ski wax")
    assert pick.search_url.endswith("q=ski wax")
    assert pick.screenshot_path
    assert pick.candidates_seen == 3


# ---- the model misbehaving ----

def test_a_hallucinated_handle_is_refused_not_fuzzy_matched():
    """The model names a product that was not in the results. Guessing which
    real product it meant would build a purchase on a hallucination."""
    llm = _FakeLLM(pick={"handle": "wax-that-does-not-exist", "reason": "great", "rejected": []})
    pick = _agent(llm=llm).shop("ski wax")
    assert not pick.found
    assert pick.handle == ""
    assert "not in the search results" in pick.reason


def test_an_over_budget_pick_is_refused_in_code_not_only_in_the_prompt():
    """The prompt says to respect the budget. A prompt is not an enforcement
    mechanism, so the ceiling is re-checked after the model answers."""
    llm = _FakeLLM(
        plan={"query": "ski wax", "max_price": 30.0, "notes": ""},
        pick={"handle": "gold-wax", "reason": "best quality", "rejected": []},
    )
    pick = _agent(llm=llm).shop("ski wax under $30")
    assert not pick.found
    assert "over the 30.00 limit" in pick.reason
    assert pick.rejected[0]["handle"] == "gold-wax"


def test_a_failed_pick_does_not_fall_back_to_first_in_stock():
    """Reverting to the old behaviour on error would make a broken model look
    like a working agent — the exact thing this replaced."""
    pick = _agent(llm=_FakeLLM(raises=True)).shop("ski wax")
    assert not pick.found
    assert "could not decide" in pick.reason


def test_rejections_naming_unknown_products_are_dropped():
    llm = _FakeLLM(pick={
        "handle": "ski-wax", "reason": "fits",
        "rejected": [
            {"handle": "gold-wax", "why": "too expensive"},
            {"handle": "invented", "why": "nonsense"},
        ],
    })
    pick = _agent(llm=llm).shop("ski wax")
    assert [r["handle"] for r in pick.rejected] == ["gold-wax"]
    assert pick.rejected[0]["title"] == "Competition Gold Wax"


# ---- scanning ----

def test_an_unreadable_results_page_falls_back_and_says_so():
    """Degrading is fine. Degrading silently is not — a catalogue lookup must
    never be presented as having scanned the site."""
    store = _FakeStore(browser_fails=True)
    pick = _agent(store=store).shop("ski wax")
    assert pick.found
    assert pick.scanned_via == "catalogue"
    assert "browser search unavailable" in pick.scan_note
    assert store.catalogue_called


def test_an_empty_results_page_is_a_real_no_result():
    """The page rendered and held nothing. That is the shop's answer, not a
    failure, so it must not silently re-ask the JSON endpoint."""
    store = _FakeStore(handles=[])
    pick = _agent(store=store).shop("hovercraft")
    assert not pick.found
    assert store.catalogue_called is False
    assert "found nothing" in pick.reason


# ---- planning ----

def test_a_failed_plan_still_searches_rather_than_giving_up():
    class _NoPlanLLM(_FakeLLM):
        def complete_json(self, system: str, user: str, **kw) -> dict:
            if "storefront search" in system:
                raise RuntimeError("planner down")
            return self._pick

    pick = _agent(llm=_NoPlanLLM()).shop("get me ski wax")
    assert pick.query == "get me ski wax"
    assert pick.found


def test_a_non_numeric_budget_is_treated_as_no_budget():
    llm = _FakeLLM(plan={"query": "ski wax", "max_price": "cheap", "notes": ""})
    pick = _agent(llm=llm).shop("ski wax, cheap")
    assert pick.max_price is None
    assert pick.found


def test_sold_out_products_are_still_shown_to_the_model():
    """The model is told what is sold out and instructed not to pick it. That
    is better than hiding them: "everything that fits is sold out" is a useful
    answer, and only the model can phrase it."""
    pick = _agent().shop("ski wax")
    assert pick.candidates_seen == 3


def test_pick_found_is_false_without_a_handle():
    assert ShoppingPick("i", "q", None, "", "", 0.0, "", "", "").found is False
