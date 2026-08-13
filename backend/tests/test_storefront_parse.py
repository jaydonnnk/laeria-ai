"""Storefront product parsing across the two endpoint shapes.

Shopify serves the same product three ways and they do not agree:

  /products.json          catalogue  — price as a decimal STRING, `available` present
  /products/{h}.json      product    — price as a decimal string, `available` MISSING
  /products/{h}.js        product    — price in CENTS,            `available` present

The middle one caused a real bug: a lookup added to fix pagination read
availability off it, got null for every product, and the shopping agent
concluded the entire catalogue was sold out. Hence `.js` and a parser that
knows about cents.
"""
from __future__ import annotations

import pytest

from services.storefront import StorefrontService


@pytest.fixture
def store(monkeypatch) -> StorefrontService:
    monkeypatch.setenv("SHOP_STORE_URL", "https://shop.example")
    from core.config import get_settings

    get_settings.cache_clear()
    svc = StorefrontService()
    yield svc
    get_settings.cache_clear()


def _js(price_cents: int, available: bool) -> dict:
    return {
        "id": 1, "handle": "ski-wax", "title": "Ski Wax", "type": "Wax",
        "vendor": "Snow Co", "tags": ["winter"],
        "featured_image": "https://img.example/wax.png",
        "variants": [
            {"id": 99, "price": price_cents, "available": available},
            {"id": 100, "price": 4995, "available": True},
        ],
    }


def test_js_prices_are_cents_not_dollars(store):
    """2495 is $24.95. Reading it as dollars would put a 24,950 ceiling on a
    card — the mandate would catch it, but only after issuing something
    absurd."""
    assert store._parse_product_js(_js(2495, True))["price_usd"] == 24.95


def test_availability_survives_the_js_shape(store):
    """The regression. Availability read as null made every product look sold
    out, and the shopping agent correctly refused to buy anything."""
    assert store._parse_product_js(_js(2495, True))["available"] is True
    assert store._parse_product_js(_js(2495, False))["available"] is False


def test_the_first_variant_is_the_one_offered(store):
    """Matches _parse_product and the variant the checkout permalink uses. The
    `.js` top-level `price` is the cheapest variant, not this one, so reading
    that instead would quote a price the cart never charges."""
    parsed = store._parse_product_js(_js(2495, True))
    assert parsed["variant_id"] == "99"
    assert parsed["price_usd"] == 24.95


def test_both_parsers_agree_on_shape(store):
    """Callers must not care which endpoint answered."""
    from_js = store._parse_product_js(_js(2495, True))
    from_catalogue = store._parse_product(
        {
            "id": 1, "handle": "ski-wax", "title": "Ski Wax",
            "product_type": "Wax", "vendor": "Snow Co", "tags": ["winter"],
            "images": [{"src": "https://img.example/wax.png"}],
            "variants": [{"id": 99, "price": "24.95", "available": True}],
        }
    )
    assert set(from_js) == set(from_catalogue)
    for key in ("handle", "title", "price_usd", "available", "variant_id", "url"):
        assert from_js[key] == from_catalogue[key], key


def test_a_product_with_no_variants_is_not_a_product(store):
    assert store._parse_product_js({"handle": "x", "variants": []}) is None


def test_an_unparseable_price_reads_as_zero_not_a_crash(store):
    """Zero is caught downstream — propose refuses a product with no readable
    price. A crash during discovery would take the whole search with it."""
    bad = _js(2495, True)
    bad["variants"][0]["price"] = "not a number"
    assert store._parse_product_js(bad)["price_usd"] == 0.0


@pytest.mark.parametrize(
    "href,expected",
    [
        ("/products/ski-wax", "ski-wax"),
        ("/collections/winter/products/ski-wax", "ski-wax"),
        ("https://shop.example/products/ski-wax?variant=99", "ski-wax"),
        ("/products/ski-wax#reviews", "ski-wax"),
        ("/products/ski-wax/", "ski-wax"),
        ("/collections/winter", ""),
        (None, ""),
    ],
)
def test_handles_are_extracted_from_every_link_shape_a_results_page_mixes(
    href, expected
):
    assert StorefrontService._handle_from_href(href) == expected
